# Three.js 0.186.1

Source: https://registry.npmjs.org/three/-/three-0.186.1.tgz

Upstream project: https://github.com/mrdoob/three.js

MIT license: LICENSE (retained verbatim). The npm tarball was verified against:

`sha512-blFeqb49wRCSGUGj7gtpfnSGHy2lwDk94RhUmS1c/hTby70kvChbWpkJ4Pm1390LqzzvTmzgXKHPEafJwCb8jA==`

Only build/three.module.js, build/three.core.js, examples/jsm/controls/OrbitControls.js and TransformControls.js are bundled. Both controls change their single bare `three` import to `./three.module.js` for self-hosted ESM without an inline import map. One upstream space-before-tab indent at three.core.js `material = getMaterial( data.material );` is normalized to tabs to pass the repository whitespace check. All other upstream code is unchanged. Runtime makes no CDN requests. Imported lazily by Harbor's viewer only on pages containing a scene.
