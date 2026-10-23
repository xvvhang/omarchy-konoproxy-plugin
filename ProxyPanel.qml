pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls.Basic as Controls
import qs.Commons
import qs.Commons as Commons
import qs.Ui
import "PanelModel.js" as Model

Panel {
    id: root
    moduleName: "konochi.konoproxy"
    manageIpc: false
    property var hostWidget: null
    property var anchorItem: null
    readonly property var service: hostWidget?.service
    readonly property var snapshot: service?.snapshot ?? ({profiles: [], groups: []})
    readonly property bool working: service?.busy ?? false
    readonly property var transition: service?.transition || null
    property string page: "Home"
    property string editProfile: ""
    property bool installConfirmation: false
    property bool removalConfirmation: false
    property bool addingProfile: false
    property string profileSource: "subscription"
    property string originalProfileSource: "subscription"
    property string deleteProfile: ""
    property bool controlConfirmation: false
    property var nodeScrolls: ({})
    readonly property var latencies: service?.latencies ?? ({})
    readonly property var group: {
        const groups = snapshot.groups || []
        return groups.find(function(g) { return root.snapshot.mode === "global" ? g.name === "GLOBAL" : g.name !== "GLOBAL" }) || groups[0] || null
    }
    function currentNode() {
        const section = (snapshot.profileGroups || []).find(function(p) { return p.active })
        return section?.nodes.find(function(n) { return n.selected })?.name || group?.now || "No node selected"
    }

    function request(value) { if (service) service.request(value) }
    function bytes(value) {
        let amount = value || 0
        const units = ["B", "KiB", "MiB", "GiB", "TiB"]
        let index = 0
        while (amount >= 1024 && index < units.length - 1) { amount /= 1024; index++ }
        return (index ? amount.toFixed(1) : Math.round(amount)) + " " + units[index]
    }
    function activeName() {
        return (snapshot.profiles || []).find(function(p) { return p.id === root.snapshot.active })?.name || "None"
    }
    function navigate(name) {
        page = name
        if (name === "Rules") customRules.text = snapshot.rules || ""
        content.contentY = 0
    }
    function beginProfile(item) {
        editProfile = item ? item.id : ""
        originalProfileSource = item?.kind === "file" ? "file" : "subscription"
        profileSource = originalProfileSource
        profileName.text = item?.name || ""
        subscriptionUrl.clear()
        profilePath.clear()
        deleteProfile = ""
        addingProfile = true
        content.contentY = 0
        profileName.forceActiveFocus()
    }
    function closeProfileForm() {
        addingProfile = false
        editProfile = ""
        profileName.clear()
        subscriptionUrl.clear()
        profilePath.clear()
    }
    onOpenedChanged: {
        if (root.service) root.service.setPanelVisible(root.opened)
        if (!opened) {
            root.closeProfileForm()
            root.deleteProfile = ""
        }
    }
    Component.onDestruction: if (service) service.setPanelVisible(false)
    Connections {
        target: root.service || null
        function onOperationFinished(action, success) {
            if ((action === "import" || action === "editProfile") && success) root.closeProfileForm()
            if (action === "delete" && success) root.deleteProfile = ""
        }
    }

    component Caption: Text {
        width: parent.width
        textFormat: Text.PlainText
        wrapMode: Text.WordWrap
        color: root.barForeground
        font.family: Style.font.family
        font.pixelSize: Style.font.body
    }
    component Hint: Caption {
        opacity: 0.65
        font.pixelSize: Style.font.caption
    }
    component Action: Controls.AbstractButton {
        id: command
        enabled: !root.working && (root.service?.ready ?? false)
        padding: Style.space(6)
        hoverEnabled: true
        contentItem: Text {
            text: command.text
            textFormat: Text.PlainText
            color: root.barForeground
            opacity: command.enabled ? command.hovered || command.visualFocus ? 1 : 0.7 : 0.3
            font.family: Style.font.family
            font.pixelSize: Style.font.bodySmall
            font.underline: command.hovered || command.visualFocus
            verticalAlignment: Text.AlignVCenter
        }
        background: Item {}
    }

    component Option: Button {
        enabled: !root.working && (root.service?.ready ?? false)
        foreground: root.barForeground
        accent: root.barForeground
        fontFamily: Style.font.family
        fontSize: Style.font.bodySmall
        horizontalPadding: Style.spacing.controlPaddingX
        verticalPadding: Style.spacing.controlPaddingY + Style.space(2)
        bordered: true
        focusable: true
        active: selected
    }
    component Input: TextField {
        width: parent.width
        enabled: !root.working
        foreground: root.barForeground
        accent: root.barForeground
        font.family: Style.font.family
        font.pixelSize: Style.font.body
    }
    component Choice: Controls.ComboBox {
        id: choice
        width: parent.width
        enabled: !root.working
        font.family: Style.font.family
        font.pixelSize: Style.font.body
        palette.text: root.barForeground
        palette.buttonText: root.barForeground
        palette.button: Qt.alpha(root.barForeground, 0.1)
        palette.base: Commons.Color.popups.background
        palette.window: Commons.Color.popups.background
        palette.highlight: Qt.alpha(root.barForeground, 0.2)
        palette.highlightedText: root.barForeground
    }
    component Check: Controls.CheckBox {
        enabled: !root.working
        font.family: Style.font.family
        font.pixelSize: Style.font.body
        palette.windowText: root.barForeground
        palette.text: root.barForeground
        palette.highlight: root.barForeground
        palette.highlightedText: Commons.Color.popups.background
    }

    component BusyMark: Text {
        id: mark
        property bool spinning: false
        visible: spinning
        width: Style.space(16)
        height: width
        text: "\u{f0450}"
        color: root.barForeground
        font.family: Style.font.family
        font.pixelSize: Style.font.body
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
        Accessible.name: "Switching"
        RotationAnimator on rotation {
            from: 0
            to: 360
            duration: 1000
            loops: Animation.Infinite
            running: mark.spinning && root.opened
        }
    }
    component RadioChoice: Option {
        id: choice
        property bool pending: false
        tooltipText: pending ? "Switching..." : ""
        Accessible.role: Accessible.RadioButton
        Accessible.checkable: true
        Accessible.checked: selected
        BusyMark {
            anchors.right: parent.right
            anchors.rightMargin: Style.space(8)
            anchors.verticalCenter: parent.verticalCenter
            spinning: choice.pending
        }
    }

    KeyboardPanel {
        borderSpec: Border.flat(Qt.alpha(root.barForeground, 0.35), Style.normalBorderWidth)
        anchorItem: root.anchorItem
        bar: root.bar
        owner: root.hostWidget
        open: root.opened
        focusTarget: content
        contentWidth: fittedContentWidth(Style.space(560))
        contentHeight: fittedContentHeight(Style.space(620))
        Column {
            id: fixedHeader
            width: parent.width
            spacing: Style.space(16)
            Row {
                width: parent.width
                spacing: Style.space(12)
                Column {
                    width: parent.width - power.implicitWidth - parent.spacing - (setupLink.visible ? setupLink.implicitWidth + parent.spacing : 0)
                    spacing: Style.space(3)
                    Caption { text: "Konoproxy"; font.pixelSize: Style.font.body * 1.35; font.bold: true }
                    Hint { text: "Profile: " + root.activeName() }
                }
                Action {
                    id: setupLink
                    visible: !root.snapshot.installed
                    text: "Set up..."
                    anchors.verticalCenter: parent.verticalCenter
                    onClicked: { root.navigate("Settings"); root.installConfirmation = true }
                }
                Item {
                    id: power
                    anchors.verticalCenter: parent.verticalCenter
                    implicitWidth: powerControl.implicitWidth
                    implicitHeight: powerControl.implicitHeight
                    ToggleSwitch {
                        id: powerControl
                        anchors.fill: parent
                        checked: !!root.snapshot.running
                        busy: root.working
                        interactive: !!root.snapshot.installed && !!root.service?.ready
                        foreground: root.barForeground
                        accent: root.barForeground
                        activeFocusOnTab: true
                        Accessible.name: root.transition?.kind === "power" ? root.transition.target ? "Starting core" : "Stopping core" : "Core running"
                        Accessible.role: Accessible.CheckBox
                        Accessible.checkable: true
                        Accessible.checked: checked
                        Keys.onSpacePressed: if (interactive && !busy) root.request({action: checked ? "stop" : "start"})
                        onToggled: root.request({action: checked ? "stop" : "start"})
                    }
                    BusyMark {
                        width: Math.max(Style.space(6), powerControl.knobSize - Style.space(4))
                        height: width
                        font.pixelSize: width
                        x: Model.knobCenter(powerControl.width, powerControl.trackWidth, powerControl.knobSize, powerControl.knobInset, powerControl.checked) - width / 2
                        anchors.verticalCenter: powerControl.verticalCenter
                        color: Commons.Color.popups.background
                        spinning: root.transition?.kind === "power"
                        Behavior on x { NumberAnimation { duration: 120; easing.type: Easing.OutCubic } }
                    }
                }
            }
            Controls.TabBar {
                id: tabs
                width: parent.width
                currentIndex: ["Home", "Nodes", "Profiles", "Rules", "Settings"].indexOf(root.page)
                onCurrentIndexChanged: {
                    const names = ["Home", "Nodes", "Profiles", "Rules", "Settings"]
                    if (currentIndex >= 0 && names[currentIndex] !== root.page) root.navigate(names[currentIndex])
                }
                spacing: 0
                background: Rectangle {
                    color: "transparent"
                    Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: Style.space(1); color: Qt.alpha(root.barForeground, 0.15) }
                }
                Repeater {
                    model: ["Home", "Nodes", "Profiles", "Rules", "Settings"]
                    Controls.TabButton {
                        id: tab
                        required property string modelData
                        width: tabs.width / 5
                        text: modelData
                        padding: Style.space(10)
                        onClicked: root.navigate(modelData)
                        contentItem: Text {
                            text: tab.text
                            color: root.barForeground
                            opacity: tab.checked ? 1 : 0.5
                            font.family: Style.font.family
                            font.pixelSize: Style.font.body
                            font.bold: tab.checked
                            horizontalAlignment: Text.AlignHCenter
                            verticalAlignment: Text.AlignVCenter
                        }
                        background: Item {
                            Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: Style.space(2); color: root.barForeground; visible: tab.checked }
                        }
                    }
                }
            }
        }
        Flickable {
            id: content
            anchors.top: fixedHeader.bottom
            anchors.topMargin: Style.space(16)
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            clip: true
            contentWidth: width
            contentHeight: column.implicitHeight
            boundsBehavior: Flickable.StopAtBounds
            Controls.ScrollBar.vertical: Controls.ScrollBar {}
            Keys.onEscapePressed: root.close()
            Column {
                id: column
                width: content.width
                spacing: Style.space(12)
                Caption {
                    visible: (root.service?.feedback || "") !== ""
                    text: root.service?.feedback || ""
                    font.pixelSize: Style.font.caption
                    opacity: root.service?.failed ? 1 : 0.7
                }
                Hint { visible: !!root.snapshot.warning; text: root.snapshot.warning || "" }

                Column {
                    width: parent.width
                    spacing: Style.space(12)
                    visible: root.page === "Home"
                    Row {
                        width: parent.width
                        spacing: Style.space(16)
                        Column {
                            width: (parent.width - parent.spacing) / 2
                            Hint { text: "UPLOAD" }
                            Caption { text: "↑ " + root.bytes(root.service?.uploadRate) + "/s"; font.pixelSize: Style.font.title }
                        }
                        Column {
                            width: (parent.width - parent.spacing) / 2
                            Hint { text: "DOWNLOAD" }
                            Caption { text: "↓ " + root.bytes(root.service?.downloadRate) + "/s"; font.pixelSize: Style.font.title }
                        }
                    }
                    Hint { text: "Session: ↑ " + root.bytes(root.snapshot.upload) + "  ·  ↓ " + root.bytes(root.snapshot.download) }
                    PanelSeparator { foreground: root.barForeground }
                    Column {
                        width: parent.width
                        spacing: Style.space(10)
                        PanelSectionHeader { text: "TRAFFIC CAPTURE"; foreground: root.barForeground; fontFamily: Style.font.family }
                        Row {
                            width: parent.width
                            spacing: Style.space(6)
                            Repeater {
                                model: [{value: "system", label: "System proxy"}, {value: "tun", label: "Tunnel proxy"}]
                                RadioChoice {
                                    required property var modelData
                                    width: (parent.width - parent.spacing) / 2
                                    text: modelData.label
                                    selected: (root.snapshot.capture || "off") === modelData.value
                                    pending: root.transition?.kind === "capture" && root.transition.target === modelData.value
                                    enabled: !root.working && !!root.snapshot.running && !!root.snapshot.active
                                    onClicked: root.request({action: "capture", value: modelData.value})
                                }
                            }
                        }
                        Hint { text: "System proxy: supported apps. Tunnel proxy: terminal and application traffic." }
                    }
                    PanelSeparator { foreground: root.barForeground }
                    Column {
                        width: parent.width
                        spacing: Style.space(10)
                        PanelSectionHeader { text: "PROXY MODE"; foreground: root.barForeground; fontFamily: Style.font.family }
                        Row {
                            width: parent.width
                            spacing: Style.space(6)
                            Repeater {
                                model: [{value: "rule", label: "Rules"}, {value: "global", label: "Global"}]
                                RadioChoice {
                                    required property var modelData
                                    width: (parent.width - Style.space(6)) / 2
                                    text: modelData.label
                                    selected: root.snapshot.mode === modelData.value
                                    pending: root.transition?.kind === "mode" && root.transition.target === modelData.value
                                    enabled: !root.working && !!root.snapshot.active
                                    onClicked: root.request({action: "mode", value: modelData.value})
                                }
                            }
                        }
                        Hint { text: "Rules follow profile and custom rules. Global uses the global policy group." }
                    }
                    PanelSeparator { foreground: root.barForeground }
                    Column {
                        width: parent.width
                        spacing: Style.space(6)
                        Hint { text: "CURRENT NODE" }
                        Caption { text: root.currentNode(); font.bold: true; maximumLineCount: 1; elide: Text.ElideRight }
                    }
                }

                Column {
                    width: parent.width
                    spacing: Style.space(10)
                    visible: root.page === "Nodes"
                    Hint { text: "Select a node to use it. Offline selections apply on the next core start." }
                    Hint { text: root.service?.probing ? "Checking node regions and latency... " + root.service.probeCompleted + "/" + root.service.probeTotal : "Regions and latency refresh once each time the panel opens." }
                    Repeater {
                        model: root.snapshot.profileGroups || []
                        Column {
                            id: groupRow
                            required property var modelData
                            width: column.width
                            spacing: Style.space(10)
                            PanelSeparator { foreground: root.barForeground }
                            Row {
                                width: parent.width
                                spacing: Style.space(8)
                                Column {
                                    width: parent.width
                                    spacing: Style.space(4)
                                    PanelSectionHeader { text: groupRow.modelData.name; foreground: root.barForeground; fontFamily: Style.font.family }
                                    Hint { text: (groupRow.modelData.nodes || []).length + " nodes" + (groupRow.modelData.active ? " · Active profile" : ""); maximumLineCount: 1; elide: Text.ElideRight }
                                }
                            }
                            Hint { visible: !!groupRow.modelData.warning; text: groupRow.modelData.warning || "" }
                            ListView {
                                id: nodeList
                                property bool restoringScroll: true
                                width: parent.width
                                height: Math.min(contentHeight, Style.space(300))
                                spacing: Style.space(6)
                                clip: true
                                boundsBehavior: Flickable.StopAtBounds
                                interactive: contentHeight > height
                                Controls.ScrollBar.vertical: Controls.ScrollBar { policy: Controls.ScrollBar.AsNeeded }
                                model: groupRow.modelData.nodes || []
                                function restoreScroll() {
                                    const position = root.nodeScrolls[groupRow.modelData.id] || 0
                                    Qt.callLater(function() {
                                        if (!nodeList) return
                                        nodeList.contentY = Math.min(position, Math.max(0, nodeList.contentHeight - nodeList.height))
                                        nodeList.restoringScroll = false
                                    })
                                }
                                Component.onCompleted: restoreScroll()
                                onModelChanged: { restoringScroll = true; restoreScroll() }
                                onContentYChanged: if (!restoringScroll) root.nodeScrolls[groupRow.modelData.id] = Math.max(0, contentY)
                                delegate: Controls.AbstractButton {
                                    id: nodeRow
                                    required property var modelData
                                    readonly property var delay: Model.delayFor(root.latencies, modelData.probeId)
                                    readonly property bool measuring: !!root.service?.probing && delay === null
                                    readonly property bool chosen: !!modelData.selected
                                    width: ListView.view.width
                                    height: Style.space(58)
                                    padding: Style.space(10)
                                    hoverEnabled: true
                                    enabled: !root.working
                                    text: modelData.name
                                    onClicked: root.request({action: "selectNode", profile: groupRow.modelData.id, name: modelData.name})
                                    background: Rectangle {
                                        radius: Style.cornerRadius
                                        color: Qt.alpha(root.barForeground, nodeRow.chosen ? 0.10 : nodeRow.hovered || nodeRow.visualFocus ? 0.05 : 0)
                                        border.color: Qt.alpha(root.barForeground, nodeRow.chosen ? 0.25 : 0)
                                    }
                                    contentItem: Row {
                                        spacing: Style.space(10)
                                        Text { text: root.service?.regionCodes?.[nodeRow.modelData.probeId] || nodeRow.modelData.regionCode || "--"; font.family: Style.font.family; font.pixelSize: Style.font.body; anchors.verticalCenter: parent.verticalCenter; width: Style.space(28); color: root.barForeground }
                                        Column {
                                            width: parent.width - Style.space(28) - (delayLabel.visible ? delayLabel.width + parent.spacing : 0) - parent.spacing
                                            spacing: Style.space(4)
                                            Caption { text: (nodeRow.chosen ? "✓  " : "") + nodeRow.modelData.name; maximumLineCount: 1; elide: Text.ElideRight }
                                            Hint { text: nodeRow.modelData.protocol + (nodeRow.modelData.regionStale && !root.service?.regionCodes?.[nodeRow.modelData.probeId] ? " · Region cache needs refresh" : ""); maximumLineCount: 1; elide: Text.ElideRight }
                                        }
                                        Item {
                                            id: delayLabel
                                            anchors.verticalCenter: parent.verticalCenter
                                            visible: nodeRow.measuring || nodeRow.delay !== null
                                            width: nodeRow.measuring ? Style.space(18) : latencyText.implicitWidth
                                            height: Style.space(18)
                                            Text {
                                                anchors.centerIn: parent
                                                visible: nodeRow.measuring
                                                text: "\u{f0450}"
                                                Accessible.name: "Measuring latency"
                                                color: root.barForeground
                                                font.family: Style.font.family
                                                font.pixelSize: Style.font.body
                                                RotationAnimator on rotation {
                                                    from: 0
                                                    to: 360
                                                    duration: 1000
                                                    loops: Animation.Infinite
                                                    running: nodeRow.measuring && root.opened && root.page === "Nodes"
                                                }
                                            }
                                            Text {
                                                id: latencyText
                                                anchors.centerIn: parent
                                                visible: !nodeRow.measuring
                                                text: nodeRow.delay > 0 ? nodeRow.delay + " ms" : "Unavailable"
                                                color: root.barForeground
                                                font.family: Style.font.family
                                                font.pixelSize: Style.font.caption
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }

                Column {
                    width: parent.width
                    spacing: Style.space(10)
                    visible: root.page === "Profiles"
                    Row {
                        width: parent.width
                        PanelSectionHeader { width: parent.width - addProfile.width; anchors.verticalCenter: parent.verticalCenter; text: root.addingProfile ? root.editProfile ? "EDIT PROFILE" : "ADD PROFILE" : "PROFILES"; foreground: root.barForeground; fontFamily: Style.font.family }
                        Action { id: addProfile; text: "+ Add profile"; visible: !root.addingProfile; enabled: !root.working && !!root.snapshot.installed; onClicked: root.beginProfile(null) }
                    }
                    Column {
                        width: parent.width
                        spacing: Style.space(10)
                        visible: root.addingProfile
                        Hint { text: root.editProfile ? "Update the name or replace this profile's source. Changes are validated before saving." : "Import a Mihomo / Clash.Meta YAML subscription or a local configuration." }
                        PanelSeparator { foreground: root.barForeground }
                        Hint { text: "NAME" }
                        Input { id: profileName; placeholderText: "Profile name"; maximumLength: 100; Accessible.name: "Profile name" }
                        Hint { text: "SOURCE" }
                        Row {
                            width: parent.width
                            spacing: Style.space(6)
                            RadioChoice { width: (parent.width - parent.spacing) / 2; text: "Subscription"; selected: root.profileSource === "subscription"; onClicked: root.profileSource = "subscription" }
                            RadioChoice { width: (parent.width - parent.spacing) / 2; text: "Local file"; selected: root.profileSource === "file"; onClicked: root.profileSource = "file" }
                        }
                        Hint { text: root.profileSource === "subscription" ? "SUBSCRIPTION URL" : "YAML FILE PATH" }
                        Input { id: subscriptionUrl; visible: root.profileSource === "subscription"; placeholderText: "https://..."; echoMode: TextInput.PasswordEchoOnEdit; Accessible.name: "HTTPS subscription URL" }
                        Input { id: profilePath; visible: root.profileSource === "file"; placeholderText: "/home/.../profile.yaml"; Accessible.name: "Local YAML file path" }
                        Hint { visible: !!root.editProfile && root.profileSource === root.originalProfileSource; text: "Leave the source field blank to keep the saved source. Saved subscription URLs are never displayed." }
                        Hint { visible: root.profileSource === "subscription"; text: "Just My Socks: use the Mihomo / Clash.Meta YAML subscription URL." }
                        PanelSeparator { foreground: root.barForeground }
                        Row {
                            spacing: Style.space(6)
                            Action { text: "Cancel"; onClicked: root.closeProfileForm() }
                            Action {
                                text: root.editProfile ? "Save changes" : "Import profile"
                                enabled: !root.working && !!root.service?.ready && !!profileName.text.trim() && ((!!root.editProfile && root.profileSource === root.originalProfileSource) || (root.profileSource === "subscription" ? !!subscriptionUrl.text.trim() : !!profilePath.text.trim()))
                                onClicked: root.request({action: root.editProfile ? "editProfile" : "import", id: root.editProfile, name: profileName.text, url: root.profileSource === "subscription" ? subscriptionUrl.text.trim() : "", path: root.profileSource === "file" ? profilePath.text.trim() : ""})
                            }
                        }
                    }
                    Hint { visible: !(root.snapshot.profiles || []).length && !root.addingProfile; text: "No profiles yet. Add a subscription or import a local YAML file." }
                    Hint { visible: !root.addingProfile && !!(root.snapshot.profiles || []).length; text: "Select a profile to use it. Edit changes its name or source; Refresh downloads the saved subscription." }
                    Repeater {
                        model: root.addingProfile ? [] : root.snapshot.profiles || []
                        Rectangle {
                            id: profileRow
                            required property var modelData
                            width: column.width
                            implicitHeight: profileBody.implicitHeight + Style.space(20)
                            radius: Style.cornerRadius
                            color: Qt.alpha(root.barForeground, profileSelection.chosen ? 0.10 : profileSelection.hovered || profileSelection.visualFocus ? 0.05 : 0)
                            border.color: Qt.alpha(root.barForeground, profileSelection.chosen ? 0.25 : 0.12)
                            Column {
                                id: profileBody
                                x: Style.space(10)
                                y: Style.space(10)
                                width: parent.width - Style.space(20)
                                spacing: Style.space(6)
                                Controls.AbstractButton {
                                    id: profileSelection
                                    readonly property bool chosen: root.snapshot.active === profileRow.modelData.id
                                    width: parent.width
                                    implicitHeight: profileLabels.implicitHeight
                                    padding: 0
                                    hoverEnabled: true
                                    enabled: !root.working && !!root.service?.ready
                                    text: profileRow.modelData.name
                                    Accessible.role: Accessible.RadioButton
                                    Accessible.checkable: true
                                    Accessible.checked: chosen
                                    onClicked: if (!chosen) root.request({action: "activate", id: profileRow.modelData.id})
                                    background: Item {}
                                    contentItem: Column {
                                        id: profileLabels
                                        spacing: Style.space(4)
                                        Caption { text: (profileSelection.chosen ? "✓  " : "") + profileRow.modelData.name; maximumLineCount: 1; elide: Text.ElideRight }
                                        Hint { text: (profileRow.modelData.kind === "subscription" ? "Subscription" : "Local file") + (profileSelection.chosen ? " · Active" : "") + " · " + new Date(profileRow.modelData.updated * 1000).toLocaleString(Qt.locale("en_US")) + (profileRow.modelData.updateError ? " · Update failed" : "") }
                                    }
                                }
                                Flow {
                                    width: parent.width
                                    spacing: Style.space(6)
                                    Action { text: "Edit..."; onClicked: root.beginProfile(profileRow.modelData) }
                                    Action { text: "Refresh"; visible: profileRow.modelData.kind === "subscription"; onClicked: root.request({action: "update", id: profileRow.modelData.id}) }
                                    Action { text: "Delete..."; enabled: !root.working && root.snapshot.active !== profileRow.modelData.id; onClicked: root.deleteProfile = profileRow.modelData.id }
                                }
                                Column {
                                    width: parent.width
                                    spacing: Style.space(6)
                                    visible: root.deleteProfile === profileRow.modelData.id
                                    Hint { text: "Delete this saved profile? This does not cancel the subscription." }
                                    Row {
                                        spacing: Style.space(6)
                                        Action { text: "Cancel"; onClicked: root.deleteProfile = "" }
                                        Action { text: "Delete profile"; onClicked: root.request({action: "delete", id: profileRow.modelData.id}) }
                                    }
                                }
                            }
                        }
                    }
                }

                Column {
                    width: parent.width
                    spacing: Style.space(10)
                    visible: root.page === "Rules"
                    PanelSectionHeader { text: "CUSTOM RULES"; foreground: root.barForeground; fontFamily: Style.font.family }
                    Hint { text: "One Clash rule per line. Takes priority over subscription rules." }
                    Controls.ScrollView {
                        width: parent.width
                        height: Style.space(180)
                        clip: true
                        Controls.TextArea {
                            id: customRules
                            enabled: !root.working && !!root.snapshot.active
                            color: root.barForeground
                            font.family: Style.font.family
                            font.pixelSize: Style.font.body
                            wrapMode: TextEdit.Wrap
                            selectByMouse: true
                            placeholderText: "DOMAIN-SUFFIX,example.com,DIRECT"
                            placeholderTextColor: Qt.alpha(root.barForeground, 0.45)
                            selectionColor: Qt.alpha(root.barForeground, 0.25)
                            selectedTextColor: root.barForeground
                            padding: Style.space(10)
                            background: Rectangle { color: "transparent"; border.color: Qt.alpha(root.barForeground, customRules.activeFocus ? 0.6 : 0.18); radius: Style.cornerRadius }
                        }
                    }
                    Action { text: "Apply rules"; enabled: !root.working && !!root.snapshot.active; onClicked: root.request({action: "rules", text: customRules.text}) }
                    PanelSeparator { foreground: root.barForeground }
                    PanelSectionHeader { text: "IMPORT RULES"; foreground: root.barForeground; fontFamily: Style.font.family }
                    Input { id: rulesPath; placeholderText: "Local rule text file path" }
                    Action { text: "Import rule file"; enabled: !root.working && !!root.snapshot.active && !!rulesPath.text; onClicked: root.request({action: "rules", path: rulesPath.text}) }
                    PanelSeparator { foreground: root.barForeground }
                    PanelSectionHeader { text: "REMOTE RULE SETS"; foreground: root.barForeground; fontFamily: Style.font.family }
                    Input { id: ruleUrl; placeholderText: "HTTPS rule-set URL"; echoMode: TextInput.PasswordEchoOnEdit }
                    Input { id: ruleTarget; placeholderText: "Target policy group, DIRECT or REJECT" }
                    Choice { id: ruleBehavior; model: ["classical", "domain", "ipcidr"] }
                    Choice { id: ruleFormat; model: ["yaml", "text", "mrs"] }
                    Action {
                        text: "Add rule set"
                        enabled: !root.working && !!root.snapshot.active && !!ruleUrl.text && !!ruleTarget.text
                        onClicked: {
                            root.request({action: "provider", url: ruleUrl.text.trim(), target: ruleTarget.text.trim(), behavior: ruleBehavior.currentText, format: ruleFormat.currentText})
                            ruleUrl.clear()
                        }
                    }
                    Repeater {
                        model: root.snapshot.ruleProviders || []
                        Hint { required property var modelData; text: "Rule set → " + modelData.target + " · " + modelData.behavior + " / " + modelData.format }
                    }
                    Action { text: "Remove custom rule sets"; enabled: !root.working && (root.snapshot.ruleProviders || []).length > 0; onClicked: root.request({action: "clearProviders"}) }
                }

                Column {
                    width: parent.width
                    spacing: Style.space(12)
                    visible: root.page === "Settings"
                    PanelSectionHeader { text: "BAR CONTENT"; foreground: root.barForeground; fontFamily: Style.font.family }
                    Repeater {
                        model: [{key: "region", label: "Node region"}, {key: "speed", label: "Upload / download speed"}]
                        Row {
                            id: barOption
                            required property var modelData
                            width: parent.width
                            spacing: Style.space(12)
                            Caption { width: parent.width - barSwitch.width - parent.spacing; anchors.verticalCenter: parent.verticalCenter; text: barOption.modelData.label }
                            ToggleSwitch {
                                id: barSwitch
                                checked: root.snapshot.barDisplay?.[barOption.modelData.key] === true
                                busy: root.working
                                interactive: !!root.service?.ready
                                foreground: root.barForeground
                                accent: root.barForeground
                                activeFocusOnTab: true
                                Accessible.name: barOption.modelData.label
                                Accessible.role: Accessible.CheckBox
                                Accessible.checkable: true
                                Accessible.checked: checked
                                Keys.onSpacePressed: if (interactive && !busy) root.request({action: "barDisplay", key: barOption.modelData.key, value: !checked})
                                onToggled: root.request({action: "barDisplay", key: barOption.modelData.key, value: !checked})
                            }
                        }
                    }
                    PanelSeparator { foreground: root.barForeground }
                    PanelSectionHeader { text: "MANAGED CORE"; foreground: root.barForeground; fontFamily: Style.font.family }
                    Action { text: "Set up Mihomo"; visible: !root.snapshot.installed; onClicked: root.installConfirmation = true }
                    Column {
                        width: parent.width
                        spacing: Style.space(8)
                        visible: root.installConfirmation && !root.snapshot.installed
                        Caption { text: "Install and start Mihomo?" }
                        Hint { text: "Downloads verified Mihomo, installs python-yaml if needed, and creates and starts a dedicated service. Requires administrator authorization. Traffic capture stays off." }
                        Check { id: autostart; text: "Start the service automatically at boot"; checked: false }
                        Check { id: passwordFree; text: "Allow password-free start, stop and restart for this service"; checked: true }
                        Hint { text: "Password-free control is limited to this service and your active local session. Installation and removal still require authorization." }
                        Hint { text: "The service can outlive the desktop session. Enabling TUN later changes routing and DNS." }
                        Flow {
                            width: parent.width
                            spacing: Style.space(6)
                            Action { text: "Cancel"; onClicked: root.installConfirmation = false }
                            Action { text: "Agree and install"; onClicked: { root.request({action: "setup", confirmed: true, autostart: autostart.checked, passwordFreeControl: passwordFree.checked}); root.installConfirmation = false } }
                        }
                    }
                    Hint { visible: !!root.snapshot.installed; text: root.snapshot.passwordFreeControl ? "Mihomo · Password-free service controls" : "Mihomo · Service controls require authentication" }
                    Action { text: "Authorize service controls..."; visible: !!root.snapshot.installed && !root.snapshot.passwordFreeControl; onClicked: root.controlConfirmation = true }
                    Column {
                        visible: root.controlConfirmation
                        width: parent.width
                        spacing: Style.space(8)
                        Hint { text: "Allow your active local session to start, stop and restart only this service? Requires one-time administrator authorization; the core is not restarted." }
                        Row {
                            spacing: Style.space(6)
                            Action { text: "Cancel"; onClicked: root.controlConfirmation = false }
                            Action { text: "Authorize"; onClicked: { root.request({action: "authorizeControl", confirmed: true}); root.controlConfirmation = false } }
                        }
                    }
                    Flow {
                        width: parent.width
                        spacing: Style.space(6)
                        Action { text: "Restart service"; visible: !!root.snapshot.installed; onClicked: root.request({action: "restart"}) }
                        Action { text: "Uninstall..."; onClicked: root.removalConfirmation = true }
                    }
                    Column {
                        width: parent.width
                        spacing: Style.space(8)
                        visible: root.removalConfirmation
                        Caption { text: "Also uninstall the managed core?" }
                        Hint { text: "Remove both: stop and disable the service, restore managed networking and remove the core. Keep core: remove only the plugin; proxying may continue. Profiles and rules are kept." }
                        Hint { text: "CLI plugin removal skips core cleanup. Use this screen for complete removal." }
                        Flow {
                            width: parent.width
                            spacing: Style.space(6)
                            Action { text: "Cancel"; onClicked: root.removalConfirmation = false }
                            Action {
                                text: "No, keep core"
                                onClicked: root.request({action: "uninstall", confirmed: true, removeCore: false, removePlugin: true})
                            }
                            Action {
                                text: "Yes, remove both"
                                onClicked: root.request({action: "uninstall", confirmed: true, removeCore: true, removePlugin: true})
                            }
                        }
                    }
                    PanelSeparator { foreground: root.barForeground }
                    PanelSectionHeader { text: "SUBSCRIPTION REFRESH"; foreground: root.barForeground; fontFamily: Style.font.family }
                    Row {
                        width: parent.width
                        spacing: Style.space(6)
                        Repeater {
                            model: [{value: 0, label: "Off"}, {value: 6, label: "6 hours"}, {value: 12, label: "12 hours"}, {value: 24, label: "24 hours"}]
                            RadioChoice {
                                required property var modelData
                                width: (parent.width - parent.spacing * 3) / 4
                                text: modelData.label
                                selected: (root.snapshot.interval ?? 24) === modelData.value
                                onClicked: if (!selected) root.request({action: "interval", value: modelData.value})
                            }
                        }
                    }
                    Hint { text: "Updates while the plugin is running. Failed updates keep the previous configuration." }
                }
            }
        }
    }
}
