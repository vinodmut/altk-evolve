"""Console-script entry points for ``evolve`` and ``evolve-mcp``.

Each applies ``--local``, ``--home`` and ``EVOLVE_PROXY`` (see :mod:`altk_evolve.local`) before
importing anything that reads Evolve's configuration, whose settings objects are built when their
modules are imported, and then hands over to the CLI or the server.
"""

import sys


def mcp_main() -> None:
    """``evolve-mcp``: the MCP server."""
    from altk_evolve.local import prepare_server

    prepare_server(sys.argv[1:])
    from altk_evolve.frontend.mcp.__main__ import main

    main()


def cli_main() -> None:
    """``evolve``: the CLI, ``evolve serve`` included."""
    from altk_evolve.local import prepare_cli

    prepare_cli(sys.argv[1:])
    from altk_evolve.cli.cli import app

    app()
