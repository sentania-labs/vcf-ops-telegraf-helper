@ECHO OFF

rem============================================================================
rem * Copyright (c) 2021-22 VMware, Inc. All Rights Reserved.
rem Script to get Guest Info for setting properties
rem============================================================================
set TELEGRAF_BIN_PATH=C:\VMware\UCP\ucp-telegraf\telegraf.exe
set "grains=C:\VMware\UCP\salt\conf\grains"
if NOT "%~1"=="" (
	set TELEGRAF_BIN_PATH=%1
)
set HNAME=%COMPUTERNAME%
set ip_address_string="IPv4"
set ips=
for /f "usebackq tokens=2 delims=:" %%f in (`ipconfig ^| findstr /c:%ip_address_string%`) do call set "ips=%%ips%%-%%f"
set ips=%ips: =%
set VM_IP=%ips:~1%
for /f "tokens=2 delims==" %%f in ('wmic os get Caption /value ^| find "="') do set "OS_NAME=%%f"
set OS_NAME=%OS_NAME: =_%

for /f "tokens=2 delims==" %%f in ('wmic os get Version /value ^| find "="') do set "OS_VERSION=%%f"
set OS_VERSION=%OS_VERSION: =_%

for /f "tokens=2" %%i in ('%TELEGRAF_BIN_PATH% --version') do set TF_VERSION=%%i
for /f "tokens=2 delims==" %%f in ('wmic bios get serialnumber /value ^| find "="') do set "SERIAL_VERSION=%%f"
set SERIAL_VERSION=%SERIAL_VERSION: =_%
for /f "tokens=2 delims==" %%f in ('wmic bios get smbiosbiosversion /value ^| find "="') do set "BIOS_VERSION=%%f"
set BIOS_VERSION=%BIOS_VERSION: =_%
set "BOOTSTRAP_FQDN=None"

rem Check if the grain file exists for product managed
if exist "%grains%" (
    rem Search for the specified key in the grains file and assign the output to a variable
	for /f "tokens=1,2 delims=: " %%a in ('findstr /i /c:"arc_fqdn:" "%grains%"') do (
	set "BOOTSTRAP_FQDN=%%b"
	)
)

set METRIC_VALUE=1
if [%HNAME%]==[] AND [%VM_IP%]==[] AND [%OS_NAME%]==[] AND [%OS_VERSION%]==[] AND [%TF_VERSION%]==[] AND [%SERIAL_VERSION%] AND [%BIOS_VERSION%] AND [%BOOTSTRAP_FQDN%](set METRIC_VALUE=0)

echo mandatory.tag,OS_NAME=%OS_NAME%,OS_VERSION=%OS_VERSION%,TELEGRAF_VERSION=%TF_VERSION%,IP=%VM_IP%,BIOS_VERSION=%BIOS_VERSION%,BOOTSTRAP_FQDN=%BOOTSTRAP_FQDN%,HOSTNAME=%HNAME% value=%METRIC_VALUE%i
