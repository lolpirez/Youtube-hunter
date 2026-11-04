import QtQuick
import QtQuick.Layouts
import NicheScope
import "." as C
import "../Utils.js" as U

// v1.0.4: one slim strip that tells the truth about transcripts.
//
//  * while the run is on its SECOND PASS (re-asking YouTube for the videos it
//    refused earlier):   ⟳ Second pass · retrying 21 refused transcripts
//  * after a run that ended with gaps:
//      ⚠ 92 / 100 transcripts · 6 no captions · 2 refused   [ Fill the gaps ]
//
// "Fill the gaps" simply runs the same scrape again: every transcript already
// saved comes from the cache instantly, only the missing ones are fetched.
// Hidden when the run was complete, so a clean run stays clean.
Rectangle {
    id: root
    signal fillRequested()

    readonly property var s: Dashboard.transcriptSummary || ({})
    readonly property int gaps: (s.missing || 0) + (s.skipped || 0)
    readonly property bool recovering: Dashboard.isRunning && Dashboard.transcriptRecovering
                                       && !Dashboard.transcriptPaused
    readonly property bool afterRun: !Dashboard.isRunning && gaps > 0

    onRecoveringChanged: if (!recovering) glyph.rotation = 0
    visible: recovering || afterRun
    implicitHeight: visible ? row.implicitHeight + Tokens.space3 * 2 : 0
    radius: Tokens.radiusLg
    color: recovering ? U.withAlpha(Theme.primary, Theme.isDark ? 0.10 : 0.06)
                      : U.withAlpha(Theme.warn, Theme.isDark ? 0.10 : 0.08)
    border.width: 1
    border.color: recovering ? U.withAlpha(Theme.primary, 0.35) : U.withAlpha(Theme.warn, 0.45)

    RowLayout {
        id: row
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.verticalCenter: parent.verticalCenter
        anchors.leftMargin: Tokens.space4
        anchors.rightMargin: Tokens.space3
        spacing: Tokens.space3

        C.IconGlyph {
            id: glyph
            name: root.recovering ? "sync" : "warning"
            fill: true
            size: 16
            glyphColor: root.recovering ? Theme.primary : Theme.warn
            RotationAnimator on rotation {
                running: root.recovering && root.visible
                loops: Animation.Infinite
                from: 0; to: 360
                duration: 1600
            }
        }

        C.AppText {
            Layout.fillWidth: true
            wrapMode: Text.Wrap
            font.pixelSize: Tokens.fontCaption + 1
            weight: Tokens.weightDemiBold
            color: Theme.on_surface
            text: root.recovering
                  ? I18n.tr("Second pass · asking YouTube again for {n} transcripts it refused earlier")
                        .replace("{n}", Dashboard.transcriptBlocked)
                  : I18n.tr("{f} / {t} transcripts · {n} have no captions · {m} missing (YouTube refused them)")
                        .replace("{f}", root.s.found || 0).replace("{t}", root.s.total || 0)
                        .replace("{n}", root.s.none || 0).replace("{m}", root.gaps)
        }

        C.PrimaryButton {
            visible: root.afterRun
            variant: "ghost"
            iconName: "refresh"
            text: I18n.tr("Fill the gaps")
            onClicked: root.fillRequested()
        }
    }
}
