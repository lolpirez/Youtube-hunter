import QtQuick
import NicheScope
import "." as C
import "../Utils.js" as U

// v1.4.7: what captions / an AI dub did to this file, worn on its thumbnail.
//
//   ╭───────────────────╮
//   │ ≡ EN  ┊  ◖) FA    │   one dark-glass capsule, a segment per step:
//   ╰───────────────────╯   captions (lines glyph) · AI dub (speaker glyph)
//
// It sits on the picture, so it is styled for media, not for the theme
// surface: dark glass, a 1 px catch-light, white mono language codes, and the
// step glyphs in the accent. When a row turns into its captioned / dubbed
// version while you are looking at it, the capsule lands with a small
// overshoot; rows that already had it never animate on a list refresh.
//
// `fx` is the row's fx map from the view-model:
//   { captions: "EN" | "", dub: "FA" | "", captionsSoft: bool, has: bool }
Item {
    id: root
    property var fx: ({})
    readonly property string capCode: (fx && fx.captions) ? String(fx.captions) : ""
    readonly property string dubCode: (fx && fx.dub) ? String(fx.dub) : ""
    readonly property bool has: capCode !== "" || dubCode !== ""

    visible: has
    implicitWidth: pill.width
    implicitHeight: pill.height
    LayoutMirroring.enabled: false
    LayoutMirroring.childrenInherit: true

    readonly property color _capTint: Qt.lighter(Theme.primary, Theme.isDark ? 1.05 : 1.3)
    readonly property color _dubTint: Qt.lighter(Theme.tertiary, Theme.isDark ? 1.05 : 1.3)

    function _lang(code) {
        var n = VoiceStudio.languageName(String(code || "").toLowerCase())
        return n !== "" ? n : String(code || "")
    }
    readonly property string tipText: {
        var parts = []
        if (capCode !== "") parts.push((fx.captionsSoft ? I18n.tr("Subtitle track") : I18n.tr("Captions")) + " · " + _lang(capCode))
        if (dubCode !== "") parts.push(I18n.tr("AI dub") + " · " + _lang(dubCode))
        return parts.join("   ")
    }

    Rectangle {
        id: pill
        height: 20
        width: row.implicitWidth + 14
        radius: height / 2
        antialiasing: true
        color: Qt.rgba(0.035, 0.035, 0.05, 0.74)
        border.width: 1
        border.color: U.withAlpha("#FFFFFF", 0.14)
        transformOrigin: Item.Right

        // catch-light along the top edge: reads as glass, not a sticker
        Rectangle {
            anchors.horizontalCenter: parent.horizontalCenter
            y: 1
            width: parent.width * 0.6; height: 1
            gradient: Gradient {
                orientation: Gradient.Horizontal
                GradientStop { position: 0.0; color: U.withAlpha("#FFFFFF", 0.0) }
                GradientStop { position: 0.5; color: U.withAlpha("#FFFFFF", 0.32) }
                GradientStop { position: 1.0; color: U.withAlpha("#FFFFFF", 0.0) }
            }
        }

        Row {
            id: row
            anchors.centerIn: parent
            spacing: 6

            Row {
                visible: root.capCode !== ""
                anchors.verticalCenter: parent.verticalCenter
                spacing: 4
                C.IconGlyph {
                    anchors.verticalCenter: parent.verticalCenter
                    name: "subject"; size: 11
                    glyphColor: root._capTint
                }
                C.AppText {
                    anchors.verticalCenter: parent.verticalCenter
                    text: root.capCode
                    mono: true
                    weight: Tokens.weightBold
                    font.pixelSize: Tokens.fontMicro
                    color: "#FFFFFF"
                }
            }
            Rectangle {
                visible: root.capCode !== "" && root.dubCode !== ""
                anchors.verticalCenter: parent.verticalCenter
                width: 1; height: 10
                color: U.withAlpha("#FFFFFF", 0.22)
            }
            Row {
                visible: root.dubCode !== ""
                anchors.verticalCenter: parent.verticalCenter
                spacing: 4
                C.IconGlyph {
                    anchors.verticalCenter: parent.verticalCenter
                    name: "volume_up"; size: 11
                    glyphColor: root._dubTint
                }
                C.AppText {
                    anchors.verticalCenter: parent.verticalCenter
                    text: root.dubCode
                    mono: true
                    weight: Tokens.weightBold
                    font.pixelSize: Tokens.fontMicro
                    color: "#FFFFFF"
                }
            }
        }
    }

    // land only when the row changes under you, never on a list rebuild
    property bool _ready: false
    Component.onCompleted: _ready = true
    onHasChanged: if (_ready && has) land.restart()
    onDubCodeChanged: if (_ready && has) land.restart()
    onCapCodeChanged: if (_ready && has) land.restart()
    ParallelAnimation {
        id: land
        NumberAnimation { target: pill; property: "scale"; from: 0.72; to: 1; duration: 420; easing.type: Easing.OutBack; easing.overshoot: 1.6 }
        NumberAnimation { target: pill; property: "opacity"; from: 0; to: 1; duration: 220; easing.type: Easing.OutCubic }
    }

    HoverHandler { id: fxHover }
    C.AppTooltip {
        visible: fxHover.hovered && root.tipText !== ""
        delay: 350
        text: root.tipText
    }
}
