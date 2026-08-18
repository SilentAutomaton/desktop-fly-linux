# Arch package for the Linux fork of DesktopFly.
# Build and install from a checkout with:  makepkg -si
#
# The connectome data is installed to /usr/share/desktop-fly/data, which is
# where desktopfly.dataset looks through $XDG_DATA_DIRS.

pkgname=desktop-fly
pkgver=0.1.0
pkgrel=1
pkgdesc='A connectome-driven 3D fruit fly on the Linux desktop (Wayland/X11 fork of DesktopFly)'
arch=('any')
url='https://github.com/DenisSergeevitch/desktop-fly'
# Code is MIT; the files under data/ are FlyWire's and are CC BY-NC 4.0, which
# makes the installed bundle non-commercial. Both texts are shipped.
license=('MIT' 'CC-BY-NC-4.0')
depends=('python' 'python-numpy' 'python-opengl' 'python-gobject' 'gtk3' 'gtk-layer-shell')
optdepends=(
  'libayatana-appindicator: tray icon and menu'
  'python-xlib: X11 and XWayland backend'
)
makedepends=('python-build' 'python-installer' 'python-setuptools' 'python-wheel')
options=('!debug')

build() {
  cd "$startdir"
  python -m build --wheel --no-isolation
}

check() {
  cd "$startdir"
  # Both suites are headless and are upstream's acceptance criteria.
  PYTHONPATH="$PWD" python -m desktopfly --simtest
  PYTHONPATH="$PWD" python -m desktopfly --behaviortest
}

package() {
  cd "$startdir"
  python -m installer --destdir="$pkgdir" dist/*.whl

  install -Dm644 data/brain_points.json data/circuit.json \
    -t "$pkgdir/usr/share/$pkgname/data"
  install -Dm644 etl.py -t "$pkgdir/usr/share/$pkgname"
  install -Dm644 config.example.toml -t "$pkgdir/usr/share/$pkgname"

  install -Dm644 LICENSE -t "$pkgdir/usr/share/licenses/$pkgname"
  install -Dm644 data/DATA_LICENSE.md -t "$pkgdir/usr/share/licenses/$pkgname"
  install -Dm644 README.md DESIGN.md third_party/UPSTREAM.md \
    -t "$pkgdir/usr/share/doc/$pkgname"
}
