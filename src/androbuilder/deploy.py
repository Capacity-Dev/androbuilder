"""Google Play upload via fastlane."""
from __future__ import annotations

from fabric import Connection

from . import ui
from .config import Config

_FASTFILE = """default_platform(:android)
platform :android do
  desc "Upload AAB to Play Console internal track"
  lane :deploy do
    version_code = Time.now.strftime("%y%m%d%H%M").to_i
    upload_to_play_store(
      track: "internal",
      release_status: "completed",
      skip_upload_metadata: true,
      skip_upload_images: true,
      skip_upload_screenshots: true,
      aab: "{aab}",
      version_code: version_code
    )
  end
end"""


def upload_to_play_store(cfg: Config, conn: Connection, build_type: str, env: dict) -> None:
    ui.subheader("Play Store Deploy")
    ui.log("Ensuring fastlane is installed …")
    conn.run(
        "command -v fastlane >/dev/null 2>&1 || "
        "(sudo apt install -y -qq ruby-full build-essential && sudo gem install fastlane -N)",
        hide=True,
        warn=True,
    )
    ui.log("Executing Fastlane deployment …")
    conn.run("mkdir -p android/fastlane")

    appfile = (
        'json_key_file "./play-store-key.json"\n'
        f'package_name "{cfg.project.package_name}"\n'
    )
    conn.run(f"cat << 'EOF' > android/fastlane/Appfile\n{appfile}EOF")

    aab = f"app/build/outputs/bundle/{build_type}/app-{build_type}.aab"
    conn.run(f"cat << 'EOF' > android/fastlane/Fastfile\n{_FASTFILE.format(aab=aab)}\nEOF")
    conn.run("cp fastlane/play-store-key.json android/play-store-key.json")

    with conn.cd("android"):
        with ui.Timer("Fastlane deploy"):
            conn.run("fastlane deploy", env=env)
    ui.ok("Deployed to Play Console (internal track)")
