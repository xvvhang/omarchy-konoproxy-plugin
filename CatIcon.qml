import QtQuick
import qs.Commons
import qs.Commons as Commons

Item {
    id: root
    property color foreground: Commons.Color.foreground
    property string fontFamily: Style.font.family
    property bool ruffled: false
    property string badge: "R"

    Canvas {
        id: cat
        anchors.fill: parent
        onWidthChanged: requestPaint()
        onHeightChanged: requestPaint()
        onPaint: {
            const ctx = getContext("2d")
            ctx.reset()
            ctx.scale(width / 24, height / 24)
            ctx.fillStyle = root.foreground
            ctx.beginPath()
            // Original, Clash-inspired cat silhouette; both states remain solid.
            ctx.moveTo(3, 1)
            ctx.lineTo(9, 5)
            ctx.quadraticCurveTo(12, 4, 15, 5)
            ctx.lineTo(21, 1)
            if (root.ruffled) {
                ctx.lineTo(21, 8)
                ctx.lineTo(24, 7)
                ctx.lineTo(22, 11)
                ctx.lineTo(24, 13)
                ctx.lineTo(22, 15)
                ctx.lineTo(24, 18)
                ctx.lineTo(20, 18)
                ctx.lineTo(21, 21)
                ctx.lineTo(17, 21)
                ctx.lineTo(16, 23)
                ctx.lineTo(8, 23)
                ctx.lineTo(7, 21)
                ctx.lineTo(3, 21)
                ctx.lineTo(4, 18)
                ctx.lineTo(0, 18)
                ctx.lineTo(2, 15)
                ctx.lineTo(0, 13)
                ctx.lineTo(2, 11)
                ctx.lineTo(0, 7)
                ctx.lineTo(3, 8)
            } else {
                ctx.lineTo(21, 11)
                ctx.bezierCurveTo(25, 18, 19, 23, 12, 23)
                ctx.bezierCurveTo(5, 23, -1, 18, 3, 11)
            }
            ctx.closePath()
            ctx.fill()
            // A transparent chest emblem stays legible on any bar background.
            ctx.globalCompositeOperation = "destination-out"
            ctx.font = 'bold 12px "' + root.fontFamily + '"'
            ctx.textAlign = "center"
            ctx.textBaseline = "middle"
            ctx.fillText(root.badge === "G" ? "G" : "R", 12, 14)
        }
    }
    onForegroundChanged: cat.requestPaint()
    onRuffledChanged: cat.requestPaint()
    onBadgeChanged: cat.requestPaint()
    onFontFamilyChanged: cat.requestPaint()
}
