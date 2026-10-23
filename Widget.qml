pragma ComponentBehavior: Bound

import QtQuick
import Quickshell.Io
import qs.Commons
import qs.Ui
import "PanelModel.js" as Model

BarWidget {
    id: root
    moduleName: "konochi.konoproxy"
    readonly property var service: bar?.shell?.serviceFor(moduleName)
    readonly property bool opened: panel.opened
    readonly property var barLabels: Model.barLabels(service?.snapshot || {}, service?.regionCodes || {}, service?.uploadRate || 0, service?.downloadRate || 0)
    readonly property var iconState: Model.iconState(service?.snapshot || {})
    readonly property bool popoutSwitchClosing: panel.popoutSwitchClosing
    readonly property real openPanelIndicatorWidth: barLabels.length ? barContents.implicitWidth : 0
    readonly property real openPanelIndicatorHeight: barLabels.length ? barContents.implicitWidth : 0
    function open() { panel.open() }
    function close() { panel.close() }
    function closeForPopoutSwitch() { panel.closeForPopoutSwitch() }
    function showPage(name) {
        if (["Home", "Nodes", "Profiles", "Rules", "Settings"].indexOf(name) === -1) return
        panel.navigate(name)
        panel.open()
    }
    IpcHandler {
        target: "konochi.konoproxy"
        function page(name: string): void { root.showPage(name) }
        function open(): void { root.open() }
        function close(): void { root.close() }
        function state(): string {
            return JSON.stringify({page: panel.page, opened: panel.opened, addingProfile: panel.addingProfile, barLabels: root.barLabels, barWidth: button.implicitWidth, contentWidth: barContents.implicitWidth, indicatorWidth: root.openPanelIndicatorWidth, iconSlot: Style.bar.iconSlot, icon: root.iconState})
        }
    }
    implicitWidth: button.implicitWidth
    implicitHeight: button.implicitHeight
    WidgetButton {
        id: button
        anchors.fill: parent
        bar: root.bar
        labelVisible: false
        hasVisualContent: true
        // Reserve the same optical slot as native icon widgets, even with no label.
        readonly property real contentExtent: barContents.implicitWidth + Math.max(0, Style.bar.iconSlot - Style.bar.iconCanvas)
        fixedWidth: vertical ? -1 : Math.max(Style.bar.iconSlot, contentExtent)
        fixedHeight: vertical ? Math.max(Style.bar.iconSlot, contentExtent) : -1
        dimmed: root.iconState.dimmed
        tooltipText: "Konoproxy · " + (root.service?.snapshot?.running ? root.service.snapshot.capture === "tun" ? "Tunnel proxy" : root.service.snapshot.capture === "system" ? "System proxy" : "No capture" : "Stopped") + " · " + (root.iconState.badge === "G" ? "Global" : "Rules")
        onPressed: function(mouseButton) { if (mouseButton === Qt.LeftButton) panel.toggle() }
        Row {
            id: barContents
            anchors.centerIn: parent
            spacing: Style.space(6)
            rotation: button.vertical ? -90 : 0
            CatIcon {
                width: Style.bar.iconCanvas
                height: width
                ruffled: root.iconState.ruffled
                badge: root.iconState.badge
                foreground: button.foreground
                fontFamily: button.fontFamily
            }
            Repeater {
                model: root.barLabels
                Text {
                    required property string modelData
                    anchors.verticalCenter: parent.verticalCenter
                    text: modelData
                    textFormat: Text.PlainText
                    color: button.foreground
                    font.family: button.fontFamily
                    font.pixelSize: Style.font.body
                    renderType: Text.NativeRendering
                }
            }
        }
    }
    ProxyPanel { id: panel; bar: root.bar; hostWidget: root; anchorItem: button }
}
