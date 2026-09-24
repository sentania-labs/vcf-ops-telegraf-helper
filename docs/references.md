# Authoritative References

This project aligns directly with Broadcom official documentation for VCF Operations open-source Telegraf integration.

## Primary Broadcom Documentation

**Title:** VCF Operations 9.1: Monitoring Applications Using Open Source Telegraf  
**URL:** https://techdocs.broadcom.com/us/en/vmware-cis/vcf/vcf-9-0-and-later/9-1/workload-monitoring-and-observability/monitoring-applications-using-open-source-telegraf.html

### Key Topic References

1. **Linux Platform Monitoring**  
   URL: https://techdocs.broadcom.com/us/en/vmware-cis/vcf/vcf-9-0-and-later/9-1/workload-monitoring-and-observability/monitoring-applications-using-open-source-telegraf/monitoring-applications-using-open-source-telegraf-on-a-linux-platform-saas-onprem.html  
   Covers the helper script workflow (`telegraf-utils.sh`), authentication token acquisition, prerequisites (jq, vmware-toolbox-cmd, uuidgen), and cloud proxy configuration.

2. **Operating System Telegraf Configurations**  
   URL: https://techdocs.broadcom.com/us/en/vmware-cis/vcf/vcf-9-0-and-later/9-1/workload-monitoring-and-observability/monitoring-applications-using-open-source-telegraf/telegraf-configuration-details-for-operating-system.html  
   Defines the exact Telegraf input plugin options required for Linux and Windows OS metrics to align with VCF Operations metric models and dashboards (e.g. CPU percpu/totalcpu/active reporting, disk mount points, network interfaces).

3. **Sample Configurations**  
   URL: https://techdocs.broadcom.com/us/en/vmware-cis/vcf/vcf-9-0-and-later/9-1/workload-monitoring-and-observability/monitoring-applications-using-open-source-telegraf/sample-scripts-open-source-telegraf.html  
   Specifies the Wavefront format HTTP output structure, cloud proxy destination URL (`/opensource/default/metric`), required headers (`uuid`, `ip`, `hostname`, or `vmId`/`vcid`), and `mandatory_tags.sh` invocation.

4. **Troubleshooting**  
   URL: https://techdocs.broadcom.com/us/en/vmware-cis/vcf/vcf-9-0-and-later/9-1/workload-monitoring-and-observability/monitoring-applications-using-open-source-telegraf/troubleshooting-open-source-telegraf.html  
   Common operational failure modes, token validity, certificate bundle issues, and log verification steps.
