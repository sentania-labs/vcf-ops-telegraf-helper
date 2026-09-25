"""CLI package."""

__all__ = ["cli"]


def __getattr__(name: str):
    if name == "cli":
        from vcf_ops_telegraf_helper.cli.main import cli

        return cli
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
