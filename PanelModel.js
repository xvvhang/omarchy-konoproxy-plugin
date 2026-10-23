function mergeDelays(current, result, session, visible) {
    if (!visible || result.latencySession !== session || !result.delays) return current
    var next = Object.assign(Object.create(null), current)
    Object.keys(result.delays).forEach(function(name) {
        var value = result.delays[name]
        if (typeof value === "number" && isFinite(value) && value >= 0) next[name] = value
    })
    return next
}

function delayFor(delays, name) {
    return Object.prototype.hasOwnProperty.call(delays, name) ? delays[name] : null
}

function mergeRegions(current, result, session, visible) {
    if (!visible || result.latencySession !== session || !result.regions) return current
    var next = Object.assign(Object.create(null), current)
    Object.keys(result.regions).forEach(function(key) {
        var code = result.regions[key]
        if (typeof code === "string" && /^[A-Z]{2}$/.test(code)) next[key] = code
    })
    return next
}

function filteredNodes(nodes, query) {
    var search = query.trim().toLowerCase()
    var rows = nodes.filter(function(node) {
        return [node.name, node.regionCode, node.country, node.protocol].join(" ").toLowerCase().indexOf(search) !== -1
    })
    return rows
}
function isQuietAction(action) {
    return ["start", "stop", "restart", "selectNode", "select", "activate", "capture", "tun", "systemProxy", "mode", "interval", "barDisplay"].indexOf(action) >= 0
}

function operationFeedback(action, ok, message) {
    if (!ok) return message || "Operation failed."
    return isQuietAction(action) || message === "Operation completed." ? "" : message || ""
}

function speedLabel(value) {
    var amount = Math.max(0, Number(value) || 0)
    var units = ["B/s", "K/s", "M/s", "G/s"]
    var index = 0
    while (amount >= 1024 && index < units.length - 1) { amount /= 1024; index++ }
    return (index ? amount.toFixed(1) : Math.round(amount)) + units[index]
}

function barLabels(snapshot, regions, uploadRate, downloadRate) {
    var options = snapshot.barDisplay || {}
    var labels = []
    if (options.region) {
        var profile = (snapshot.profileGroups || []).find(function(item) { return item.active })
        var node = (profile?.nodes || []).find(function(item) { return item.selected })
        labels.push((node && (regions[node.probeId] || node.regionCode)) || "--")
    }
    if (options.speed) labels.push("↑" + speedLabel(snapshot.running ? uploadRate : 0) + " ↓" + speedLabel(snapshot.running ? downloadRate : 0))
    return labels
}

function iconState(snapshot) {
    return {ruffled: snapshot.running === true && snapshot.capture === "tun",
            dimmed: snapshot.running !== true || snapshot.capture === "off" || !snapshot.capture,
            badge: snapshot.mode === "global" ? "G" : "R"}
}

function knobCenter(width, trackWidth, knobSize, knobInset, checked) {
    return (width - trackWidth) / 2 + (checked ? trackWidth - knobSize - knobInset : knobInset) + knobSize / 2
}

function transitionFor(request) {
    if (["start", "stop", "restart"].indexOf(request.action) >= 0)
        return {kind: "power", target: request.action !== "stop", confirming: false}
    if (request.action === "capture" || request.action === "mode")
        return {kind: request.action, target: request.value, confirming: false}
    return null
}

function transitionConfirmed(transition, snapshot) {
    if (!transition || !transition.confirming) return false
    if (transition.kind === "power") return snapshot.running === transition.target
    return snapshot[transition.kind] === transition.target
}
