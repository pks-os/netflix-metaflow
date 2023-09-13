import json
import os
import shutil
import subprocess
import sys

from metaflow.metaflow_config import DATASTORE_LOCAL_DIR
from metaflow.plugins import DATASTORES

from . import MAGIC_FILE, _datastore_packageroot

# Bootstraps a valid conda virtual environment composed of conda and pypi packages

if __name__ == "__main__":
    if len(sys.argv) != 5:
        print("Usage: bootstrap.py <flow_name> <id> <datastore_type> <architecture>")
        sys.exit(1)
    _, flow_name, id_, datastore_type, architecture = sys.argv

    prefix = os.path.join(os.getcwd(), id_)
    pkgs_dir = os.path.join(os.getcwd(), ".pkgs")
    manifest_dir = os.path.join(os.getcwd(), DATASTORE_LOCAL_DIR, flow_name)

    datastores = [d for d in DATASTORES if d.TYPE == datastore_type]
    if not datastores:
        print(f"No datastore found for type: {datastore_type}")
        sys.exit(1)
    storage = datastores[0](_datastore_packageroot(datastore_type))

    # Move MAGIC_FILE inside local datastore.
    os.makedirs(manifest_dir, exist_ok=True)
    shutil.move(
        os.path.join(os.getcwd(), MAGIC_FILE),
        os.path.join(manifest_dir, MAGIC_FILE),
    )

    with open(os.path.join(manifest_dir, MAGIC_FILE)) as f:
        env = json.load(f)[id_][architecture]

    # Download Conda packages.
    conda_pkgs_dir = os.path.join(pkgs_dir, "conda")
    with storage.load_bytes([package["path"] for package in env["conda"]]) as results:
        for key, tmpfile, _ in results:
            # Ensure that conda packages go into architecture specific folders.
            # The path looks like REPO/CHANNEL/CONDA_SUBDIR/PACKAGE. We trick
            # Micromamba into believing that all packages are coming from a local
            # channel - the only hurdle is ensuring that packages are organised
            # properly.
            dest = os.path.join(conda_pkgs_dir, "/".join(key.split("/")[-2:]))
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.move(tmpfile, dest)

    # Create Conda environment.
    cmds = [
        # TODO: check if mamba or conda are already available on the image
        f"""if ! command -v ./micromamba >/dev/null 2>&1; then
            wget -qO- https://micro.mamba.pm/api/micromamba/{architecture}/latest | tar -xvj bin/micromamba --strip-components=1;
            export PATH=$PATH:$HOME/bin;
            if ! command -v ./micromamba >/dev/null 2>&1; then
                echo "Failed to install Micromamba!";
                exit 1;
            fi;
        fi""",
        # Create a conda environment through Micromamba.
        f'''tmpfile=$(mktemp);
        echo "@EXPLICIT" > "$tmpfile";
        ls -d {conda_pkgs_dir}/*/* >> "$tmpfile";
        ./micromamba create --yes --offline --no-deps --safety-checks=disabled --prefix {prefix} --file "$tmpfile";
        rm "$tmpfile"''',
    ]

    # Download PyPI packages.
    if "pypi" in env:
        pypi_pkgs_dir = os.path.join(pkgs_dir, "pypi")
        with storage.load_bytes(
            [package["path"] for package in env["pypi"]]
        ) as results:
            for key, tmpfile, _ in results:
                dest = os.path.join(pypi_pkgs_dir, os.path.basename(key))
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                shutil.move(tmpfile, dest)

        # Install PyPI packages.
        cmds.extend(
            [
                f"""./micromamba run --prefix {prefix} pip install --root-user-action=ignore {pypi_pkgs_dir}/*.whl"""
            ]
        )

    for cmd in cmds:
        result = subprocess.run(
            cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        if result.returncode != 0:
            print(f"Bootstrap failed while executing: {cmd}")
            print("Stdout:", result.stdout.decode())
            print("Stderr:", result.stderr.decode())
            sys.exit(1)