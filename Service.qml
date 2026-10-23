import QtQuick
import Quickshell.Io
import "PanelModel.js" as Model

Item {
    id: root
    property var snapshot: ({profiles: [], groups: [], installed: false, running: false, active: "", tun: false, systemProxy: false, mode: "rule", interval: 24})
    property bool busy: false
    property bool ready: false
    property bool panelVisible: false
    property var latencies: ({})
    property var regionCodes: ({})
    property bool probing: false
    property int probeCompleted: 0
    property int probeTotal: 0
    property int latencySession: 0
    property string lastAction: ""
    property var transition: null
    signal operationFinished(string action, bool success)
    property string feedback: ""
    property bool failed: false
    property real uploadRate: 0
    property real downloadRate: 0
    property real previousTime: 0
    property real previousUpload: 0
    property real previousDownload: 0

    function request(value) {
        if (!ready || busy) return
        busy = true
        failed = false
        feedback = Model.isQuietAction(value.action) ? "" : value.action === "resolveRegions" ? "Resolving node exit regions..." : "Working..."
        lastAction = value.action
        transition = Model.transitionFor(value)
        const request = Object.assign({}, value)
        if (value.action.indexOf("delay") === 0) request.latencySession = latencySession
        backend.write(JSON.stringify(request) + "\n")
    }

    function setPanelVisible(value) {
        if (panelVisible === value) return
        latencySession++
        probing = value
        probeCompleted = 0
        probeTotal = 0
        if (!value) {
            latencies = ({})
            regionCodes = ({})
            probing = false
            if (lastAction.indexOf("delay") === 0) feedback = ""
        }
        panelVisible = value
        syncVisibility()
    }

    function syncVisibility() {
        if (ready) backend.write(JSON.stringify({action: "visible", value: panelVisible, latencySession: latencySession}) + "\n")
    }

    Process {
        id: backend
        command: ["/usr/bin/python3", "-B", Qt.resolvedUrl("backend.py").toString().replace(/^file:\/\//, ""), "serve"]
        stdinEnabled: true
        running: true
        onStarted: {
            root.ready = true
            root.syncVisibility()
        }
        stdout: SplitParser {
            onRead: function(line) {
                try {
                    const event = JSON.parse(line)
                    if (event.event === "probe") {
                        if (root.panelVisible && event.latencySession === root.latencySession) {
                            root.latencies = Model.mergeDelays(root.latencies, event, root.latencySession, true)
                            root.regionCodes = Model.mergeRegions(root.regionCodes, event, root.latencySession, true)
                            root.probeCompleted = event.completed
                            root.probeTotal = event.total
                            root.probing = event.completed < event.total
                        }
                    } else if (event.event === "state" && event.ok) {
                        const next = event.result
                        const now = Date.now() / 1000
                        const elapsed = now - root.previousTime
                        root.uploadRate = root.previousTime && elapsed > 0 ? Math.max(0, next.upload - root.previousUpload) / elapsed : 0
                        root.downloadRate = root.previousTime && elapsed > 0 ? Math.max(0, next.download - root.previousDownload) / elapsed : 0
                        root.previousUpload = next.upload
                        root.previousDownload = next.download
                        root.previousTime = now
                        if (JSON.stringify(next.profileGroups) === JSON.stringify(root.snapshot.profileGroups)) next.profileGroups = root.snapshot.profileGroups
                        root.snapshot = next
                        if (Model.transitionConfirmed(root.transition, next)) {
                            confirmationTimeout.stop()
                            root.transition = null
                            root.busy = false
                        }
                    } else if (event.event === "result") {
                        if (event.ok && root.transition) {
                            root.transition = Object.assign({}, root.transition, {confirming: true})
                            confirmationTimeout.restart()
                        } else {
                            root.transition = null
                            root.busy = false
                            confirmationTimeout.stop()
                        }
                        root.failed = !event.ok
                        root.feedback = Model.operationFeedback(event.action || root.lastAction, event.ok, event.ok ? event.result.message : event.error)
                        if (event.ok) root.latencies = Model.mergeDelays(root.latencies, event.result, root.latencySession, root.panelVisible)
                        if ((event.action || "").indexOf("delay") === 0 && (!root.panelVisible || event.result?.latencySession !== root.latencySession)) root.feedback = ""
                        root.operationFinished(event.action || root.lastAction, event.ok)
                        root.syncVisibility()
                    } else if (!event.ok) {
                        root.failed = true
                        root.feedback = event.error
                    }
                } catch (error) {
                    root.feedback = "Invalid backend response."
                    root.failed = true
                }
            }
        }
        stderr: StdioCollector {
            onStreamFinished: {
                if (text.trim()) console.warn("Konoproxy backend reported an internal error.")
            }
        }
        onExited: {
            root.ready = false
            root.busy = false
            root.transition = null
            confirmationTimeout.stop()
            root.latencies = ({})
            root.regionCodes = ({})
            root.probing = false
            root.latencySession++
            root.feedback = "Backend stopped. Retrying..."
            retry.restart()
        }
    }
    Timer { id: retry; interval: 5000; onTriggered: backend.running = true }
    Timer {
        id: confirmationTimeout
        interval: 15000
        onTriggered: {
            root.transition = null
            root.busy = false
            root.failed = true
            root.feedback = "The requested state could not be confirmed. Check the current core and capture settings."
        }
    }
    IpcHandler {
        target: "konochi.konoproxy.backend"
        function status(): string {
            return JSON.stringify({ready: root.ready, busy: root.busy, transition: root.transition, panelVisible: root.panelVisible, probing: root.probing, probeSession: root.latencySession, probeCompleted: root.probeCompleted, probeTotal: root.probeTotal, latencies: root.latencies, data: root.snapshot})
        }
    }
}
