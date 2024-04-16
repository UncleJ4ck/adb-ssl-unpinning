#!/usr/bin/env python3

import os
import shutil
import subprocess as sp
from pathlib import Path
from sys import argv, exit
import requests
from datetime import datetime
from defusedxml import ElementTree as ET
from adbutils import AdbClient, AdbDevice

# ADB Configuration
ADB_HOST = '127.0.0.1'
ADB_PORT = 5037

APKTOOL_URL = "https://api.github.com/repos/iBotPeaches/Apktool/releases/latest"
UBER_APK_SIGNER_URL = "https://api.github.com/repos/patrickfav/uber-apk-signer/releases/latest"

UTILS_DIR = Path(__file__).resolve().parent / "utils"
UTILS_DIR.mkdir(exist_ok=True)

PACKAGES_DIR = Path(__file__).resolve().parent / "packages"
PACKAGES_DIR.mkdir(exist_ok=True)
PATCHED_DIR = Path(__file__).resolve().parent / "patched"
PATCHED_DIR.mkdir(exist_ok=True)

def debug_log(message):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}")

def download_latest_jar(download_url):
    debug_log(f"Requesting latest release from {download_url}")
    response = requests.get(download_url)
    response.raise_for_status()
    release_info = response.json()
    jar_asset = next((item for item in release_info["assets"] if item['name'].endswith('.jar')), None)
    if not jar_asset:
        raise Exception("JAR file is missing in the release assets.")
    jar_file_path = UTILS_DIR / jar_asset['name']
    if jar_file_path.exists():
        debug_log(f"Deleting existing file {jar_file_path}")
        jar_file_path.unlink()
    debug_log(f"Downloading {jar_asset['name']}...")
    jar_response = requests.get(jar_asset['browser_download_url'])
    jar_response.raise_for_status()
    with open(jar_file_path, 'wb') as f:
        f.write(jar_response.content)
    return jar_file_path

def pull_package(device: AdbDevice, package_name: str, output_path: Path):
    debug_log(f"Pulling APKs for package: {package_name}")
    apks = device.shell(f'pm path {package_name}').splitlines()
    if not apks:
        print('Package not found')
        exit(1)
    output_path.mkdir(parents=True, exist_ok=True)
    for apk in apks:
        apk_path = apk.split(':', 1)[1]
        debug_log(f"Pulling {apk_path}...")
        device.sync.pull(apk_path, output_path / Path(apk_path).name)

def patch_manifest(unpacked_apk_path: Path):
    debug_log(f"Patching manifest at {unpacked_apk_path}")
    manifest_path = unpacked_apk_path / "AndroidManifest.xml"
    tree = ET.parse(str(manifest_path))
    root = tree.getroot()
    application = root.find(".//application")
    ns = {"android": "http://schemas.android.com/apk/res/android"}
    if application.get(f"{{{ns['android']}}}networkSecurityConfig") is None:
        application.set(f"{{{ns['android']}}}networkSecurityConfig", "@xml/network_security_config")
        tree.write(manifest_path, encoding='utf-8', xml_declaration=True)

def add_network_security_config(unpacked_apk_path: Path):
    debug_log(f"Adding network security config at {unpacked_apk_path}")
    config_path = unpacked_apk_path / "res" / "xml"
    config_path.mkdir(parents=True, exist_ok=True)
    with open(config_path / "network_security_config.xml", "w") as f:
        f.write('''<?xml version="1.0" encoding="utf-8"?>
<network-security-config>
    <debug-overrides>
        <trust-anchors>
            <certificates src="user" />
        </trust-anchors>
    </debug-overrides>
    <base-config cleartextTrafficPermitted="true">
        <trust-anchors>
            <certificates src="system" />
            <certificates src="user" />
        </trust-anchors>
    </base-config>
</network-security-config>
''')

def patch_package(device: AdbDevice, package_name: str, apktool_jar: Path, signer_jar: Path):
    debug_log(f"Starting patch process for {package_name}")
    original_output = PACKAGES_DIR / package_name
    pull_package(device, package_name, original_output)
    patched_output = PATCHED_DIR / f"{package_name}_patched"
    shutil.rmtree(patched_output, ignore_errors=True)
    patched_output.mkdir()
    for apk in original_output.iterdir():
        file_name = apk.stem
        unpacked_apk_path = patched_output / file_name
        packed_apk_path = patched_output / f"{file_name}.repack.apk"
        signed_apk_path = patched_output / f"{file_name}.repack-aligned-debugSigned.apk"
        debug_log(f"Unpacking {file_name}")
        sp.run(["java", "-jar", str(apktool_jar), "d", str(apk), "-o", str(unpacked_apk_path), "-s"], check=True)
        if file_name == 'base':
            patch_manifest(unpacked_apk_path)
            add_network_security_config(unpacked_apk_path)
        debug_log(f"Repacking {file_name}")
        sp.run(["java", "-jar", str(apktool_jar), "b", str(unpacked_apk_path), "-o", str(packed_apk_path)], check=True)
        debug_log(f"Signing {file_name}")
        sp.run(["java", "-jar", str(signer_jar), "-a", str(packed_apk_path)], check=True)
        os.remove(packed_apk_path)
        shutil.rmtree(unpacked_apk_path)
        signed_apk_path.rename(patched_output / f"{file_name}_patched.apk")
    device.uninstall(package_name)
    debug_log('Uninstalled original APKs')
    print('Installing patched APKs...')
    apk_files = [str(apk) for apk in patched_output.glob("*.apk")]
    debug_log(f"Installing APKs: {apk_files}")
    sp.run(['adb', 'install-multiple'] + apk_files, check=True)

if __name__ == '__main__':
    if len(argv) < 3:
        print('Usage: python3 patch_apk.py <serial> <package_name>')
        exit(1)
    client = AdbClient(host=ADB_HOST, port=ADB_PORT)
    device = client.device(argv[1])
    apktool_jar = download_latest_jar(APKTOOL_URL)
    signer_jar = download_latest_jar(UBER_APK_SIGNER_URL)
    package_name = argv[2].removeprefix('package:')
    patch_package(device, package_name, apktool_jar, signer_jar)