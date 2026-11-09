#!/usr/bin/env python3
"""Generate and verify THIRD-PARTY-NOTICES for bundled binary packages.

Compliance with LGPLv3 (PySide6/Qt), LGPLv2.1 (Paramiko), Apache 2.0 (cryptography, tzdata),
and other open-source licenses bundled into vcf-ops-telegraf-helper binaries.
"""

from __future__ import annotations

import argparse
import importlib.metadata as md
from pathlib import Path
import sys
from typing import Dict, List, Optional, Set, Tuple

from packaging.requirements import Requirement

# Repo root
REPO_ROOT = Path(__file__).resolve().parent.parent

# Standard licenses embedded or read from scripts/licenses/
LICENSES_DIR = REPO_ROOT / "scripts" / "licenses"


def _read_license_file(filename: str) -> str:
    text = (LICENSES_DIR / filename).read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError(f"License text file is empty: {filename}")
    return text


# Standard texts
APACHE_2_0_TEXT = _read_license_file("Apache-2.0.txt")
LGPL_3_0_TEXT = _read_license_file("LGPL-3.0.txt")
GPL_3_0_TEXT = _read_license_file("GPL-3.0.txt")
LGPL_2_1_TEXT = _read_license_file("LGPL-2.1.txt")

# Canonical fallback metadata for packages where dist-info RECORD / license files are not populated
# (e.g., Debian/Ubuntu system packages or minimal wheels)
CANONICAL_FALLBACKS: Dict[str, Tuple[str, str, str]] = {
    "click": (
        "BSD-3-Clause",
        "Copyright 2014 Pallets",
        """Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are
met:

1.  Redistributions of source code must retain the above copyright
    notice, this list of conditions and the following disclaimer.

2.  Redistributions in binary form must reproduce the above copyright
    notice, this list of conditions and the following disclaimer in the
    documentation and/or other materials provided with the distribution.

3.  Neither the name of the copyright holder nor the names of its
    contributors may be used to endorse or promote products derived from
    this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
"AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A
PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT
HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED
TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR
PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF
LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING
NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.""",
    ),
    "rich": (
        "MIT",
        "Copyright (c) 2020 Will McGugan",
        """Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.""",
    ),
    "pygments": (
        "BSD-2-Clause",
        "Copyright (c) 2006-2026 by the Pygments team.",
        """Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are
met:

* Redistributions of source code must retain the above copyright
  notice, this list of conditions and the following disclaimer.

* Redistributions in binary form must reproduce the above copyright
  notice, this list of conditions and the following disclaimer in the
  documentation and/or other materials provided with the distribution.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
"AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR
A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT
OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT
LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY
THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
(INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.""",
    ),
    "pydantic": (
        "MIT",
        "Copyright (c) 2017 to present, Samuel Colvin and other contributors",
        """Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.""",
    ),
    "pydantic_core": (
        "MIT",
        "Copyright (c) 2022 to present, Samuel Colvin and other contributors",
        """Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.""",
    ),
    "annotated_types": (
        "MIT",
        "Copyright (c) 2023 Adrian Garcia Badaracco",
        """Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.""",
    ),
    "typing_inspection": (
        "MIT",
        "Copyright (c) 2023 Jelle Zijlstra",
        """Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.""",
    ),
    "requests": (
        "Apache-2.0",
        "Copyright 2019 Kenneth Reitz",
        APACHE_2_0_TEXT,
    ),
    "urllib3": (
        "MIT",
        "Copyright (c) 2008-2026 Andrey Petrov and contributors",
        """Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.""",
    ),
    "idna": (
        "BSD-3-Clause",
        "Copyright (c) 2013-2026, Kim Davies. All rights reserved.",
        """Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

    * Redistributions of source code must retain the above copyright
      notice, this list of conditions and the following disclaimer.

    * Redistributions in binary form must reproduce the above copyright
      notice, this list of conditions and the following disclaimer in the
      documentation and/or other materials provided with the distribution.

    * Neither the name of the copyright holder nor the names of the
      contributors may be used to endorse or promote products derived from
      this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE CONTRIBUTORS "AS IS" AND ANY EXPRESS
OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDERS OR CONTRIBUTORS BE
LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
POSSIBILITY OF SUCH DAMAGE.""",
    ),
    "certifi": (
        "MPL-2.0",
        "Mozilla Public License Version 2.0 (Certifi CA Bundle)",
        """This Source Code Form is subject to the terms of the Mozilla Public
License, v. 2.0. If a copy of the MPL was not distributed with this
file, You can obtain one at https://mozilla.org/MPL/2.0/.

This package contains the Mozilla CA Certificate root program.""",
    ),
    "chardet": (
        "LGPL-2.1",
        "Copyright (C) Mark Pilgrim, Dan Blanchard",
        LGPL_2_1_TEXT,
    ),
    "cryptography": (
        "Apache-2.0 / BSD-3-Clause",
        "Copyright (c) Individual contributors. Dual licensed under Apache-2.0 and BSD-3-Clause.",
        f"""Licensed under the Apache License, Version 2.0 or BSD 3-Clause License.

--- Apache License, Version 2.0 ---
{APACHE_2_0_TEXT}

--- BSD 3-Clause License ---
Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice, this
   list of conditions and the following disclaimer.
2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.
3. Neither the name of the copyright holder nor the names of its contributors
   may be used to endorse or promote products derived from this software without
   specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.""",
    ),
    "bcrypt": (
        "Apache-2.0",
        "Copyright (c) The bcrypt developers",
        APACHE_2_0_TEXT,
    ),
    "markdown_it_py": (
        "MIT",
        "Copyright (c) 2020 Executable Book Project",
        """Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.""",
    ),
    "mdurl": (
        "MIT",
        "Copyright (c) 2021 Taneli Hukkinen",
        """Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.""",
    ),
}


def collect_bundled_distributions() -> Dict[str, md.Distribution]:
    """Collect all distributions frozen into the standalone helper binaries."""
    visited: Set[str] = set()
    to_visit: List[Tuple[str, Optional[str]]] = [("vcf-ops-telegraf-helper", "gui")]
    dists: Dict[str, md.Distribution] = {}

    while to_visit:
        curr, ext = to_visit.pop(0)
        norm = curr.lower().replace("-", "_").replace(".", "_")
        if norm in visited:
            continue
        visited.add(norm)
        try:
            d = md.distribution(curr)
        except Exception:
            continue
        pkg_name = d.metadata.get("Name") or curr
        dists[pkg_name] = d

        for r_str in (d.requires or []):
            try:
                req = Requirement(r_str)
                if req.marker:
                    env = {"extra": ext or ""}
                    if not req.marker.evaluate(env):
                        continue
                to_visit.append((req.name, None))
            except Exception:
                continue

    # Also include packages explicitly frozen by build_binary
    for explicit in ["tzdata", "winrm", "truststore", "cryptography", "PySide6"]:
        norm = explicit.lower().replace("-", "_").replace(".", "_")
        if norm not in visited:
            try:
                d = md.distribution(explicit)
                dists[d.metadata.get("Name") or explicit] = d
                visited.add(norm)
            except Exception:
                pass

    return dists


def get_distribution_license_text(pkg_name: str, dist: md.Distribution) -> Tuple[str, str]:
    """Return (license_type, full_license_text) for a distribution."""
    norm = pkg_name.lower().replace("-", "_").replace(".", "_")

    if norm == "vcf_ops_telegraf_helper":
        return ("MIT", (REPO_ROOT / "LICENSE").read_text(encoding="utf-8").strip())

    # Special handling for PySide6 / Qt suite
    if norm.startswith("pyside6") or norm == "shiboken6":
        return (
            "LGPL-3.0-only / GPL-3.0-only",
            f"""PySide6 and Qt are licensed under the GNU Lesser General Public License (LGPL)
version 3, with portions licensed under the GNU General Public License (GPL) version 3.
Copyright (C) The Qt Company Ltd. and other contributors.

--- GNU LESSER GENERAL PUBLIC LICENSE Version 3 ---
{LGPL_3_0_TEXT}

--- GNU GENERAL PUBLIC LICENSE Version 3 ---
{GPL_3_0_TEXT}""",
        )

    # Special handling for paramiko
    if norm == "paramiko":
        return (
            "LGPL-2.1-or-later",
            f"""Paramiko is licensed under the GNU Lesser General Public License (LGPL) version 2.1.
Copyright (C) Jeff Forcier, Robey Pointer, and others.

--- GNU LESSER GENERAL PUBLIC LICENSE Version 2.1 ---
{LGPL_2_1_TEXT}""",
        )

    # Try reading from installed wheel .dist-info license files
    texts: List[str] = []
    if dist.files:
        for f in dist.files:
            if Path(str(f)).name.upper().startswith(("LICENSE", "COPYING", "NOTICE")):
                try:
                    path = dist.locate_file(f)
                    if path.is_file():
                        content = path.read_text(encoding="utf-8", errors="replace").strip()
                        if content:
                            texts.append(f"[{f}]\n{content}")
                except Exception:
                    pass

    if texts:
        license_meta = dist.metadata.get("License") or dist.metadata.get("License-Expression") or "See notice below"
        return (license_meta, "\n\n".join(texts))

    # Check canonical fallback dictionary
    if norm in CANONICAL_FALLBACKS:
        lic_name, copyright_header, full_text = CANONICAL_FALLBACKS[norm]
        combined = f"{copyright_header}\n\n{full_text}" if copyright_header else full_text
        return (lic_name, combined)

    license_meta = dist.metadata.get("License") or dist.metadata.get("License-Expression") or "Unknown"
    return (license_meta, "")


def generate_notices_text(dists: Optional[Dict[str, md.Distribution]] = None) -> str:
    """Generate the full THIRD-PARTY-NOTICES text."""
    if dists is None:
        dists = collect_bundled_distributions()

    pyside_ver = dists.get("PySide6", None)
    pyside_version_str = pyside_ver.version if pyside_ver else "6.11.3"
    qt_major_minor = ".".join(pyside_version_str.split(".")[:2])

    paramiko_dist = dists.get("paramiko", None)
    paramiko_version_str = paramiko_dist.version if paramiko_dist else "5.1.0"

    vcf_dist = dists.get("vcf-ops-telegraf-helper", None)
    vcf_version_str = vcf_dist.version if vcf_dist else "0.8.8"

    lines: List[str] = []

    lines.append("=" * 80)
    lines.append("THIRD-PARTY SOFTWARE NOTICES AND LICENSES")
    lines.append("VCF Operations Open Telegraf Helper")
    lines.append(f"Version: {vcf_version_str}")
    lines.append("=" * 80)
    lines.append("")
    lines.append("This standalone distribution is licensed under the MIT License:")
    lines.append("")
    lines.append("MIT License")
    lines.append("Copyright (c) 2026 Scott")
    lines.append("")
    lines.append("Permission is hereby granted, free of charge, to any person obtaining a copy")
    lines.append('of this software and associated documentation files (the "Software"), to deal')
    lines.append("in the Software without restriction, including without limitation the rights")
    lines.append("to use, copy, modify, merge, publish, distribute, sublicense, and/or sell")
    lines.append("copies of the Software, and to permit persons to whom the Software is")
    lines.append("furnished to do so, subject to the following conditions:")
    lines.append("")
    lines.append("The above copyright notice and this permission notice shall be included in all")
    lines.append("copies or substantial portions of the Software.")
    lines.append("")
    lines.append('THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR')
    lines.append("IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,")
    lines.append("FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE")
    lines.append("AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER")
    lines.append("LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,")
    lines.append("OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE")
    lines.append("SOFTWARE.")
    lines.append("")
    lines.append("=" * 80)
    lines.append("SOURCE CODE AVAILABILITY AND RELINKING INSTRUCTIONS")
    lines.append("=" * 80)
    lines.append("")
    lines.append("This software bundles third-party open-source libraries licensed under the")
    lines.append("GNU Lesser General Public License (LGPL):")
    lines.append(f"  - PySide6 / Qt v{pyside_version_str} (GNU LGPL version 3 / GNU GPL version 3)")
    lines.append(f"  - Paramiko v{paramiko_version_str} (GNU LGPL version 2.1)")
    lines.append("")
    lines.append("In compliance with LGPLv3 Section 4(d)(0) and LGPLv2.1 Section 6, complete")
    lines.append("source code and reproducible relinking/rebuilding instructions are available.")
    lines.append("Because this application is distributed as an open-source application with public")
    lines.append("source code and automated PyInstaller build definitions, users may modify the")
    lines.append("LGPL components (PySide6/Qt, Paramiko) and rebuild or relink the binary executable.")
    lines.append("")
    lines.append("Application Source Repository:")
    lines.append("  https://github.com/sentania-labs/vcf-ops-telegraf-helper")
    lines.append("")
    lines.append("Build and Relinking Procedure:")
    lines.append("  1. Clone the project repository:")
    lines.append("       git clone https://github.com/sentania-labs/vcf-ops-telegraf-helper.git")
    lines.append("       cd vcf-ops-telegraf-helper")
    lines.append("  2. Install your desired/modified version of PySide6, Paramiko, and build tools:")
    lines.append("       python -m pip install -e '.[gui]' pyinstaller")
    lines.append("  3. Build the single executable for your target platform:")
    lines.append("       python scripts/build_binary.py [linux|windows|macos-arm64]")
    lines.append("     The output binary is produced in the dist/ directory.")
    lines.append("")
    lines.append("Exact Upstream Source Code Archives for Bundled LGPL Components:")
    lines.append(f"  - PySide6 v{pyside_version_str}:")
    lines.append(f"      https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-{pyside_version_str}-src/")
    lines.append(f"      https://code.qt.io/cgit/pyside/pyside-setup.git/tag/?h=v{pyside_version_str}")
    lines.append(f"      https://pypi.org/project/PySide6/{pyside_version_str}/#files")
    lines.append(f"  - Qt Sources (version {pyside_version_str}):")
    lines.append(f"      https://download.qt.io/archive/qt/{qt_major_minor}/{pyside_version_str}/submodules/")
    lines.append("      https://code.qt.io/cgit/")
    lines.append(f"  - Paramiko v{paramiko_version_str}:")
    lines.append(f"      https://github.com/paramiko/paramiko/archive/refs/tags/{paramiko_version_str}.tar.gz")
    lines.append(f"      https://pypi.org/project/paramiko/{paramiko_version_str}/#files")
    lines.append("")
    lines.append("=" * 80)
    lines.append("BUNDLED THIRD-PARTY PACKAGES SUMMARY")
    lines.append("=" * 80)
    lines.append("")

    package_entries: List[Tuple[str, str, str, str]] = []
    for pkg_name in sorted(dists.keys(), key=lambda s: s.lower()):
        d = dists[pkg_name]
        lic_type, lic_text = get_distribution_license_text(pkg_name, d)
        package_entries.append((pkg_name, d.version, lic_type, lic_text))

    lines.append(f"{'Package':<28} {'Version':<14} {'License'}")
    lines.append("-" * 78)
    for pkg_name, ver, lic_type, _ in package_entries:
        lines.append(f"{pkg_name:<28} {ver:<14} {lic_type}")
    lines.append("")

    lines.append("=" * 80)
    lines.append("DETAILED THIRD-PARTY LICENSES AND NOTICES")
    lines.append("=" * 80)
    lines.append("")

    for pkg_name, ver, lic_type, lic_text in package_entries:
        lines.append("-" * 80)
        lines.append(f"Package: {pkg_name} {ver}")
        lines.append(f"License: {lic_type}")
        lines.append("-" * 80)
        lines.append(lic_text.strip() if lic_text.strip() else "[NO LICENSE TEXT LOCATED]")
        lines.append("")
        lines.append("")

    return "\n".join(lines)


def verify_notices(notices_text: str, dists: Dict[str, md.Distribution]) -> None:
    """Verify that every bundled distribution has valid license text and requirements are met."""
    errors: List[str] = []

    # 1. Source pointer check
    if "SOURCE CODE AVAILABILITY AND RELINKING INSTRUCTIONS" not in notices_text:
        errors.append("Missing source code availability and relinking instructions header")
    if "https://github.com/sentania-labs/vcf-ops-telegraf-helper" not in notices_text:
        errors.append("Missing repository source pointer URL")
    if "https://download.qt.io/official_releases/QtForPython/pyside6/" not in notices_text:
        errors.append("Missing exact PySide6 source pointer URL")
    if "https://github.com/paramiko/paramiko/" not in notices_text:
        errors.append("Missing exact Paramiko source pointer URL")

    # 2. LGPL / GPL checks
    if "GNU LESSER GENERAL PUBLIC LICENSE Version 3" not in notices_text:
        errors.append("Missing LGPL-3.0 full license text for PySide6/Qt")
    if "GNU GENERAL PUBLIC LICENSE Version 3" not in notices_text:
        errors.append("Missing GPL-3.0 full license text for PySide6/Qt")
    if "GNU LESSER GENERAL PUBLIC LICENSE Version 2.1" not in notices_text:
        errors.append("Missing LGPL-2.1 full license text for Paramiko")

    # 3. Check every bundled distribution
    for pkg_name, d in dists.items():
        marker = f"Package: {pkg_name} {d.version}"
        if marker not in notices_text:
            errors.append(f"Distribution '{pkg_name}' ({d.version}) not listed in notices")
            continue

        # Find package section
        start = notices_text.find(marker)
        next_pkg = notices_text.find("Package: ", start + len(marker))
        section = notices_text[start:next_pkg] if next_pkg != -1 else notices_text[start:]

        if "[NO LICENSE TEXT LOCATED]" in section:
            errors.append(f"Distribution '{pkg_name}' ({d.version}) has no license text!")
            continue

        # Extract body after header lines
        header_end = section.find("-" * 80, len(marker))
        body = section[header_end + 80:].strip() if header_end != -1 else ""
        if len(body) < 40:
            errors.append(f"Distribution '{pkg_name}' ({d.version}) has insufficient license text (len={len(body)})")

    if errors:
        msg = f"THIRD-PARTY-NOTICES verification failed with {len(errors)} error(s):\n" + "\n".join(f"  - {e}" for e in errors)
        raise RuntimeError(msg)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate and verify THIRD-PARTY-NOTICES.txt")
    parser.add_argument("--output", "-o", type=Path, default=None, help="Output file path")
    parser.add_argument("--verify", action="store_true", help="Verify only; do not write any files")
    args = parser.parse_args()

    dists = collect_bundled_distributions()
    print(f"Collected {len(dists)} bundled distributions.")

    text = generate_notices_text(dists)

    # Verification
    try:
        verify_notices(text, dists)
        print("Verification passed: every bundled distribution contains full license text and source pointers.")
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    if args.verify:
        return

    outputs = []
    if args.output:
        outputs.append(args.output)
    else:
        outputs.append(REPO_ROOT / "vcf_ops_telegraf_helper" / "THIRD-PARTY-NOTICES.txt")

    for out in outputs:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        print(f"Wrote {len(text)} bytes to {out}")


if __name__ == "__main__":
    main()
