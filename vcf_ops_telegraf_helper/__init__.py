"""VCF Operations Open Telegraf Helper.

A local administrator utility for configuring open-source Telegraf for VCF Operations.
"""

try:
    from vcf_ops_telegraf_helper._version import __version__
except ImportError:
    from importlib.metadata import PackageNotFoundError, version
    try:
        __version__ = version("vcf-ops-telegraf-helper")
    except PackageNotFoundError:
        __version__ = "0.0.0+source"
