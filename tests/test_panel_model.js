const assert = require("node:assert/strict")
const fs = require("node:fs")
const vm = require("node:vm")
const path = require("node:path")

const model = vm.createContext({})
vm.runInContext(fs.readFileSync(path.join(__dirname, "..", "PanelModel.js"), "utf8"), model)
for (const action of ["start", "stop", "restart", "selectNode", "activate", "capture", "mode", "interval"]) {
    assert.equal(model.isQuietAction(action), true)
    assert.equal(model.operationFeedback(action, true, "Operation completed."), "")
    assert.equal(model.operationFeedback(action, false, "Could not apply."), "Could not apply.")
}
assert.equal(model.isQuietAction("editProfile"), false)
assert.equal(model.operationFeedback("import", true, "Imported."), "Imported.")
assert.equal(model.operationFeedback("import", true, "Operation completed."), "")
assert.equal(model.operationFeedback("import", true), "")
assert.equal(model.operationFeedback("import", false, "Import failed."), "Import failed.")
assert.equal(model.isQuietAction("barDisplay"), true)

assert.equal(model.barLabels({}, {}, 0, 0).length, 0)
const barState = {
    running: true, capture: "tun", mode: "global",
    profileGroups: [{active: true, nodes: [{selected: true, probeId: "node-id", regionCode: "JP"}]}],
}
const barKeys = ["region", "speed"]
const expectedLabels = ["JP", "↑1.0K/s ↓2.0K/s"]
for (let mask = 0; mask < 4; mask++) {
    const barDisplay = Object.fromEntries(barKeys.map((key, i) => [key, !!(mask & (1 << i))]))
    const labels = model.barLabels({...barState, barDisplay}, {}, 1024, 2048)
    assert.equal(JSON.stringify(labels), JSON.stringify(expectedLabels.filter((_, i) => mask & (1 << i))))
}
assert.equal(model.barLabels({...barState, barDisplay: {region: true}}, {"node-id": "UK"}, 0, 0)[0], "UK")
assert.equal(model.barLabels({barDisplay: {region: true}}, {}, 0, 0)[0], "--")
assert.equal(model.barLabels({running: false, barDisplay: {speed: true}}, {}, 1024, 2048)[0], "↑0B/s ↓0B/s")
assert.equal(model.barLabels({capture: "system", mode: "rule", barDisplay: {capture: true, mode: true}}, {}, 0, 0).length, 0)
for (const mode of ["rule", "global"]) {
    for (const capture of ["off", "system", "tun"]) {
        for (const running of [true, false]) {
            const icon = model.iconState({mode, capture, running})
            assert.equal(icon.badge, mode === "rule" ? "R" : "G")
            assert.equal(icon.ruffled, running && capture === "tun")
            assert.equal(icon.dimmed, !running || capture === "off")
        }
    }
}
assert.equal(model.iconState({}).ruffled, false)
assert.equal(model.iconState({}).badge, "R")
assert.equal(model.knobCenter(62, 42, 16, 3, false), 21)
assert.equal(model.knobCenter(62, 42, 16, 3, true), 41)
for (const checked of [false, true]) {
    const center = model.knobCenter(80, 60, 24, 4, checked)
    assert.ok(center - 12 >= 10)
    assert.ok(center + 12 <= 70)
}

const catQml = fs.readFileSync(path.join(__dirname, "..", "CatIcon.qml"), "utf8")
const paintBody = catQml.match(/onPaint:\s*\{([\s\S]*?)\n        \}/)[1]
const paint = new Function("root", "width", "height", "getContext", paintBody)
for (const ruffled of [false, true]) {
    for (const badge of ["R", "G"]) {
        const calls = []
        const ctx = new Proxy({}, {
            get: (_, method) => (...args) => calls.push([method, ...args]),
            set: (object, key, value) => { object[key] = value; calls.push([key, value]); return true },
        })
        paint({ruffled, badge, foreground: "#ffffff", fontFamily: "sans-serif"}, 16, 16, () => ctx)
        assert.equal(calls.filter(call => call[0] === "fill").length, 1)
        assert.equal(calls.filter(call => call[0] === "stroke").length, 0)
        assert.ok(calls.some(call => call[0] === "globalCompositeOperation" && call[1] === "destination-out"))
        assert.ok(calls.some(call => JSON.stringify(call) === JSON.stringify(["fillText", badge, 12, 14])))
        assert.equal(calls.some(call => call[0] === "lineTo" && call[1] === 24), ruffled)
    }
}

for (const [request, before, after] of [
    [{action: "start"}, {running: false}, {running: true}],
    [{action: "stop"}, {running: true}, {running: false}],
    [{action: "restart"}, {running: false}, {running: true}],
    [{action: "capture", value: "tun"}, {capture: "system"}, {capture: "tun"}],
    [{action: "capture", value: "system"}, {capture: "tun"}, {capture: "system"}],
    [{action: "mode", value: "global"}, {mode: "rule"}, {mode: "global"}],
]) {
    const pending = model.transitionFor(request)
    assert.equal(model.transitionConfirmed(pending, before), false)
    assert.equal(model.transitionConfirmed(pending, after), false)
    const confirming = {...pending, confirming: true}
    assert.equal(model.transitionConfirmed(confirming, before), false)
    assert.equal(model.transitionConfirmed(confirming, after), true)
}
assert.equal(model.transitionFor({action: "barDisplay"}), null)
assert.equal(model.transitionConfirmed(null, {}), false)

const previous = {A: 10}
const result = {latencySession: 2, delays: {B: 42}}
const next = model.mergeDelays(previous, result, 2, true)
assert.equal(next.A, 10)
assert.equal(next.B, 42)
assert.equal(previous.B, undefined)
assert.equal(model.mergeDelays(previous, result, 2, false), previous)
assert.equal(model.mergeDelays(previous, result, 3, true), previous)
assert.equal(model.delayFor({}, "constructor"), null)

const unsafeNames = JSON.parse('{"__proto__":17,"constructor":21,"bad":"42","negative":-1}')
const safe = model.mergeDelays({}, {latencySession: 1, delays: unsafeNames}, 1, true)
assert.equal(Object.getPrototypeOf(safe), null)
assert.equal(model.delayFor(safe, "__proto__"), 17)
assert.equal(model.delayFor(safe, "constructor"), 21)
assert.equal(model.delayFor(safe, "bad"), null)
assert.equal(model.delayFor(safe, "negative"), null)
const regions = model.mergeRegions({}, {latencySession: 2, regions: {A: "JP", B: "not-a-code"}}, 2, true)
assert.equal(regions.A, "JP")
assert.equal(regions.B, undefined)
assert.equal(model.mergeRegions(regions, {latencySession: 1, regions: {A: "US"}}, 2, true), regions)
assert.equal(model.mergeRegions(regions, {latencySession: 2, regions: {A: "US"}}, 2, false), regions)

const nodes = [
    {name: "Slow", country: "Japan", protocol: "SS"},
    {name: "Fast", country: "Hong Kong", protocol: "VLESS"},
    {name: "Timed out", country: "Unknown", protocol: "VMess"},
]
assert.equal(model.filteredNodes(nodes, "hong kong").length, 1)
assert.equal(model.filteredNodes(nodes, "vless")[0].name, "Fast")
const ordered = model.filteredNodes(nodes, "")
assert.equal(ordered[0].name, "Slow")
assert.equal(ordered[2].name, "Timed out")
assert.equal(nodes[0].name, "Slow")
console.log("Panel model tests passed.")
