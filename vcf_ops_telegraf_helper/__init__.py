"""VCF Operations Open Telegraf Helper.

A local administrator utility for configuring open-source Telegraf for VCF Operations.
"""

try:
    from vcf_ops_telegraf_helper._version import __version__
except ImportError:
    from importlib.metadata import version
    __version__ = version("vcf-ops-telegraf-helper")
