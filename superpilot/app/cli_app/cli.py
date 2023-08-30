from pathlib import Path

import click
import yaml

from superpilot.core.runner.cli_app.main import run_superpilot
from superpilot.core.runner.client_lib.shared_click_commands import (
    DEFAULT_SETTINGS_FILE,
    make_settings,
)
from superpilot.core.runner.client_lib.utils import coroutine, handle_exceptions


@click.group()
def autogpt():
    """Temporary command group for v2 commands."""
    pass


autogpt.add_command(make_settings)


@autogpt.command()
@click.option(
    "--settings-file",
    type=click.Path(),
    default=DEFAULT_SETTINGS_FILE,
)
@click.option(
    "--pdb",
    is_flag=True,
    help="Drop into a debugger if an error is raised.",
)
@coroutine
async def run(settings_file: str, pdb: bool) -> None:
    """Run the Auto-GPT pilot."""
    click.echo("Running Auto-GPT pilot...")
    settings_file = Path(settings_file)
    settings = {}
    if settings_file.exists():
        settings = yaml.safe_load(settings_file.read_text())
    main = handle_exceptions(run_superpilot, with_debugger=pdb)
    await main(settings)


if __name__ == "__main__":
    autogpt()
