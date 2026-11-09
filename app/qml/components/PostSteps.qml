import QtQuick
import NicheScope
import "." as C
import "../Utils.js" as U

// v1.4: the steps of one download / cut, in a row:
//   ✓ Download   ◐ Captions 42%   ○ AI dub
// Fed by Cutter.postSteps / Downloads.live[id].steps. Plain items only (no
// layers, no timers): the spinner is one RotationAnimator on the render thread.
Row {
    id: root
    property var steps: []
    property int fontSize: Tokens.fontCaption
    spacing: Tokens.space2
    LayoutMirroring.enabled: false

    Repeater {
        model: root.steps
        Row {
            id: chip
            required property var modelData
            required property int index
            readonly property string st: modelData.state || "queued"
            readonly property bool run: st === "running"
            spacing: 5
            Rectangle {
                visible: chip.index > 0
                width: 10; height: 1
                anchors.verticalCenter: parent.verticalCenter
                color: Theme.border
            }
            Item {
                width: 14; height: 14
                anchors.verticalCenter: parent.verticalCenter
                Rectangle {
                    anchors.fill: parent
                    radius: 7
                    color: chip.st === "done" ? Theme.secondary_container
                         : chip.st === "failed" ? Theme.error
                         : "transparent"
                    border.width: chip.st === "done" || chip.st === "failed" ? 0 : 1.5
                    border.color: chip.run ? U.withAlpha(Theme.primary, 0.25) : Theme.outline
                }
                // running: an accent arc chasing round the ring
                Rectangle {
                    visible: chip.run
                    anchors.fill: parent
                    radius: 7
                    color: "transparent"
                    border.width: 1.5
                    border.color: "transparent"
                    Rectangle {
                        width: 4; height: 4; radius: 2
                        color: Theme.primary
                        x: parent.width / 2 - 2; y: -1
                    }
                    RotationAnimator on rotation {
                        running: chip.run && chip.visible
                        from: 0; to: 360; duration: 900
                        loops: Animation.Infinite
                    }
                }
                C.IconGlyph {
                    visible: chip.st === "done" || chip.st === "failed"
                    anchors.centerIn: parent
                    name: chip.st === "done" ? "check" : "close"
                    size: 10
                    glyphColor: "#FFFFFF"
                }
            }
            C.AppText {
                anchors.verticalCenter: parent.verticalCenter
                text: (chip.modelData.label || "")
                      + (chip.run ? "  " + Math.round((chip.modelData.pct || 0) * 100) + "%" : "")
                weight: chip.run ? Tokens.weightBold : Tokens.weightMedium
                font.pixelSize: root.fontSize
                color: chip.run ? Theme.primary
                     : chip.st === "done" ? Theme.text_strong
                     : chip.st === "failed" ? Theme.error
                     : chip.st === "cancelled" ? Theme.warn
                     : Theme.on_surface_var
            }
        }
    }
}
