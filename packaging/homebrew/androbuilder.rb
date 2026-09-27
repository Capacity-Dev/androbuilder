# Homebrew formula for androbuilder (tap-style).
#
#   brew install Capacity-Dev/tap/androbuilder
#
# Place this file in a `homebrew-tap` repository under `Formula/androbuilder.rb`.
# After each PyPI release: update `url`/`sha256` and regenerate the Python
# resources with `brew update-python-resources androbuilder`.
class Androbuilder < Formula
  include Language::Python::Virtualenv

  desc "Build and deploy Expo/React Native Android apps on ephemeral EC2"
  homepage "https://github.com/Capacity-Dev/androbuilder"
  url "https://files.pythonhosted.org/packages/source/a/androbuilder-cli/androbuilder_cli-0.1.0.tar.gz"
  sha256 "REPLACE_WITH_SDIST_SHA256"
  license "MIT"

  depends_on "python@3.12"

  # Python resources are generated automatically:
  #   brew update-python-resources androbuilder
  # (botocore[crt], boto3, fabric, typer, rich, tqdm, tomli-w, ...)

  def install
    virtualenv_install_with_resources
  end

  test do
    assert_match "androbuilder", shell_output("#{bin}/androbuilder --version")
  end
end
