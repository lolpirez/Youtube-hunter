import QtQuick
import QtQuick.Layouts
import NicheScope
import "." as C
import "../Utils.js" as U

// v1.0.4: the app-wide twin of the Scraper's "IP blocked" card.
//
//   ╭──────────────────────────────────────────────────────────╮
//   │ ● Transcripts paused · IP blocked   1:47   Fix it  →     │
//   ╰──────────────────────────────────────────────────────────╯
//
// Floats at the top of the page area on every OTHER page while a scrape is
// paused, so leaving the Scraper never hides the stop. One click jumps back
// to the card (page 0) where the fix lives.
Item {
    id: root
    readonly property bool wanted: Dashboard.transcriptPaused && Dashboard.isRunning
                                   && App.currentPage !== 0
    readonly property int cooldown: Dashboard.transcriptCooldown

    function clock(s) {
        s = Math.max(0, s | 0)
        const m = Math.floor(s / 60), r = s % 60
        return m + ":" + (r < 10 ? "0" : "") + r
    }

    width: pill.width
    height: pill.height
    visible: opacity > 0.01
    opacity: wanted ? 1 : 0
    Behavior on opacity { NumberAnimation { duration: Tokens.durSlow; easing.type: Easing.OutCubic } }
    property real _dy: wanted ? 0 : -12
    Behavior on _dy { NumberAnimation { duration: Tokens.durSlow; easing.type: Easing.OutBack } }
    transform: Translate { y: root._dy }

    Rectangle {
        id: pill
        width: pillRow.implicitWidth + Tokens.space4 * 2
        height: 44
        radius: height / 2
        color: Theme.isDark ? U.withAlpha(Theme.surface_highest, 0.96) : U.withAlpha(Theme.surface, 0.98)
        border.width: 1
        border.color: hover.hovered ? U.withAlpha(Theme.error, 0.75) : U.withAlpha(Theme.error, 0.45)
        Behavior on border.color { ColorAnimation { duration: Tokens.durFast } }
        antialiasing: true

        // soft alert wash on the leading side
        Rectangle {
            anchors.fill: parent
            radius: parent.radius
            gradient: Gradient {
                orientation: Gradient.Horizontal
                GradientStop { position: 0.0; color: U.withAlpha(Theme.error, Theme.isDark ? 0.16 : 0.09) }
                GradientStop { position: 0.6; color: U.withAlpha(Theme.error, 0) }
            }
        }

        RowLayout {
            id: pillRow
            anchors.centerIn: parent
            spacing: Tokens.space3

            Item {
                implicitWidth: 10; implicitHeight: 10
                Rectangle {
                    id: halo
                    anchors.fill: parent
                    radius: 5
                    color: Theme.error
                    opacity: 0
                    ParallelAnimation {
                        running: root.wanted
                        loops: Animation.Infinite
                        ScaleAnimator { target: halo; from: 1; to: 2.6; duration: 1300; easing.type: Easing.OutCubic }
                        OpacityAnimator { target: halo; from: 0.55; to: 0; duration: 1300; easing.type: Easing.OutCubic }
                    }
                }
                Rectangle { anchors.fill: parent; radius: 5; color: Theme.error }
            }

            C.AppText {
                text: I18n.tr("Transcripts paused · IP blocked")
                weight: Tokens.weightBold
                font.pixelSize: Tokens.fontCaption + 1
                color: Theme.text_strong
            }

            Rectangle {
                visible: root.cooldown > 0
                implicitWidth: cdText.implicitWidth + Tokens.space2 * 2
                implicitHeight: 24
                radius: 12
                color: U.withAlpha(Theme.error, 0.12)
                C.AppText {
                    id: cdText
                    anchors.centerIn: parent
                    text: root.clock(root.cooldown)
                    mono: true
                    weight: Tokens.weightBold
                    font.pixelSize: Tokens.fontCaption
                    color: Theme.error
                    LayoutMirroring.enabled: false
                }
            }

            Row {
                spacing: Tokens.space1
                C.AppText {
                    anchors.verticalCenter: parent.verticalCenter
                    text: I18n.tr("Fix it")
                    weight: Tokens.weightExtraBold
                    font.pixelSize: Tokens.fontCaption + 1
                    color: Theme.primary
                }
                C.IconGlyph {
                    id: arrowNudge
                    anchors.verticalCenter: parent.verticalCenter
                    name: "arrow_forward"
                    size: 14
                    glyphColor: Theme.primary
                    property real _nudge: hover.hovered ? 3 : 0
                    Behavior on _nudge { NumberAnimation { duration: Tokens.durFast } }
                    transform: Translate { x: arrowNudge._nudge }
                }
            }
        }

        HoverHandler { id: hover; cursorShape: Qt.PointingHandCursor }
        TapHandler { onTapped: App.goto(0) }
    }
}
