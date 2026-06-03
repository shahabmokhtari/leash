"""First-run console installer for Leash."""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, TextIO

from leash.config import ConfigurationManager, create_default_configuration, resolve_config_path
from leash.exceptions import ConfigurationException
from leash.models.permission_profile import BUILTIN_PROFILES

InputFunc = Callable[[], str]


@dataclass(frozen=True)
class InstallerOption:
    """A selectable option shown in the console installer."""

    key: str
    label: str
    description: str
    aliases: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class InstallerSelection:
    """The choices captured by the console installer."""

    profile_key: str
    enforcement_mode: str


PROFILE_OPTIONS: tuple[InstallerOption, ...] = (
    InstallerOption(
        key="trust",
        label=BUILTIN_PROFILES["trust"].name,
        description=BUILTIN_PROFILES["trust"].description,
    ),
    InstallerOption(
        key="permissive",
        label=BUILTIN_PROFILES["permissive"].name,
        description=BUILTIN_PROFILES["permissive"].description,
    ),
    InstallerOption(
        key="moderate",
        label=BUILTIN_PROFILES["moderate"].name,
        description=BUILTIN_PROFILES["moderate"].description,
    ),
)

ENFORCEMENT_OPTIONS: tuple[InstallerOption, ...] = (
    InstallerOption(
        key="observe",
        label="Observe",
        description="Log only, no decisions.",
    ),
    InstallerOption(
        key="approve-only",
        label="Approve-Only",
        description="Auto-approve safe requests, never deny them.",
        aliases=("approve",),
    ),
    InstallerOption(
        key="enforce",
        label="Enforce",
        description="Approve or deny requests based on the analysis.",
    ),
)


def should_run_installer(
    config_path: str | Path | None,
    *,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
) -> bool:
    """Return True when the first-run installer should be shown."""
    input_stream = stdin or sys.stdin
    output_stream = stdout or sys.stdout
    return _is_interactive_stream(input_stream) and _is_interactive_stream(output_stream) and not resolve_config_path(
        config_path
    ).exists()


def run_console_installer(
    *,
    config_path: str | Path | None,
    no_hooks: bool,
    profile_default: str = "moderate",
    enforcement_default: str = "observe",
    input_func: InputFunc | None = None,
    output: TextIO | None = None,
) -> InstallerSelection:
    """Run the console installer and persist the user's selections."""
    prompt_input = input_func or input
    stream = output or sys.stdout

    try:
        stream.write("Leash setup\n")
        stream.write("===========\n\n")
        stream.write("Choose your default security settings. You can change them later in the dashboard.\n\n")

        profile = _prompt_for_selection(
            title="Select security profile",
            options=PROFILE_OPTIONS,
            default_key=profile_default,
            input_func=prompt_input,
            output=stream,
        )
        enforcement = _prompt_for_selection(
            title="Select enforcement mode",
            options=ENFORCEMENT_OPTIONS,
            default_key=enforcement_default,
            input_func=prompt_input,
            output=stream,
        )
    except (EOFError, KeyboardInterrupt):
        stream.write("\nSetup cancelled.\n")
        stream.flush()
        raise SystemExit(1) from None

    selection = InstallerSelection(profile_key=profile.key, enforcement_mode=enforcement.key)
    stream.write("Saving configuration...\n")
    stream.flush()
    try:
        asyncio.run(_persist_selection(config_path=config_path, selection=selection))
    except ConfigurationException as exc:
        stream.write(f"Setup failed: {exc}\n")
        stream.flush()
        raise SystemExit(1) from None

    if no_hooks:
        stream.write("Skipping hook installation (--no-hooks).\n")
    else:
        stream.write("Installing hooks...\n")
    stream.write(
        f"Selected profile: {profile.label}\n"
        f"Selected mode: {enforcement.label}\n"
        "Starting Leash...\n\n"
    )
    stream.flush()
    return selection


def _prompt_for_selection(
    *,
    title: str,
    options: tuple[InstallerOption, ...],
    default_key: str,
    input_func: InputFunc,
    output: TextIO,
) -> InstallerOption:
    selected = _find_default_option(options, default_key)

    while True:
        output.write(f"{title}:\n")
        for option in options:
            marker = "[x]" if option.key == selected.key else "[ ]"
            output.write(f"{marker} {option.label}\n")

        output.write("\n")
        for index, option in enumerate(options, start=1):
            suffix = " (default)" if option.key == selected.key else ""
            output.write(f"  {index}. {option.label} - {option.description}{suffix}\n")

        output.write(f"\nChoose [1-{len(options)}] or press Enter for {selected.label}: ")
        output.flush()
        raw = input_func().strip().lower()
        output.write("\n")

        if not raw:
            return selected

        if raw.isdigit():
            index = int(raw) - 1
            if 0 <= index < len(options):
                return options[index]
        else:
            for option in options:
                accepted_values = {option.key, option.label.lower(), *option.aliases}
                if raw in accepted_values:
                    return option

        output.write("Please enter a valid option number or name.\n\n")


async def _persist_selection(*, config_path: str | Path | None, selection: InstallerSelection) -> None:
    manager = ConfigurationManager(
        config_path=resolve_config_path(config_path),
        config=create_default_configuration(),
    )
    config = manager.get_configuration()
    config.profiles.active_profile = selection.profile_key
    config.enforcement_mode = selection.enforcement_mode
    config.enforcement_enabled = selection.enforcement_mode == "enforce"
    await manager.save()


def _find_default_option(options: tuple[InstallerOption, ...], default_key: str) -> InstallerOption:
    for option in options:
        if option.key == default_key:
            return option
    return options[0]


def _is_interactive_stream(stream: TextIO | None) -> bool:
    isatty = getattr(stream, "isatty", None)
    return bool(isatty and isatty())
