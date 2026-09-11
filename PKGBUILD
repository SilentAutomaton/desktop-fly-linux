# Arch package for the Linux fork of DesktopFly.
# Build and install from a checkout with:  makepkg -si
#
# The connectome data is installed to /usr/share/desktop-fly/data, which is
# where desktopfly.dataset looks through $XDG_DATA_DIRS.

pkgname=desktop-fly
pkgver=0.2.1
pkgrel=1
pkgdesc='A connectome-driven 3D fruit fly on the Linux desktop (Wayland/X11 fork of DesktopFly)'
arch=('any')
url='https://github.com/SilentAutomaton/desktop-fly-linux'
# Code is MIT. The data is two sources under two licences: the FlyWire files
# are CC BY-NC 4.0, which makes the installed bundle non-commercial, and the
# MaleCNS files are CC BY 4.0. All three texts are shipped.
license=('MIT' 'CC-BY-NC-4.0' 'CC-BY-4.0')
depends=('python' 'python-numpy' 'python-opengl' 'python-gobject' 'gtk3' 'gtk-layer-shell')
optdepends=(
  'libayatana-appindicator: tray icon and menu'
  'python-xlib: X11 and XWayland backend'
)
makedepends=('python-build' 'python-installer' 'python-setuptools' 'python-wheel')
options=('!debug')

build() {
  cd "$startdir"
  # There is no source=(), so this builds in the checkout. Without the clean a
  # second build finds the first one's wheel still in dist/ and package() then
  # hands two wheels to installer, which dies on the duplicate files.
  rm -rf dist build
  python -m build --wheel --no-isolation
}

check() {
  cd "$startdir"
  # All three suites are headless and are upstream's acceptance criteria.
  PYTHONPATH="$PWD" python -m desktopfly --simtest
  PYTHONPATH="$PWD" python -m desktopfly --behaviortest
  PYTHONPATH="$PWD" python -m desktopfly --locomotortest
}

package() {
  cd "$startdir"
  python -m installer --destdir="$pkgdir" "dist/desktop_fly_linux-$pkgver-py3-none-any.whl"

  install -Dm644 data/brain_points.json data/circuit.json data/locomotor_circuit.json \
    -t "$pkgdir/usr/share/$pkgname/data"
  install -Dm644 data/locomotor_report.json -t "$pkgdir/usr/share/$pkgname/data"
  install -Dm644 etl.py etl_malecns.py -t "$pkgdir/usr/share/$pkgname"
  install -Dm644 config.example.toml -t "$pkgdir/usr/share/$pkgname"

  install -Dm644 LICENSE -t "$pkgdir/usr/share/licenses/$pkgname"
  install -Dm644 data/DATA_LICENSE.md data/LOCOMOTOR_PROVENANCE.md \
    -t "$pkgdir/usr/share/licenses/$pkgname"
  install -Dm644 README.md DESIGN.md EVALUATION.md third_party/UPSTREAM.md \
    -t "$pkgdir/usr/share/doc/$pkgname"
}
