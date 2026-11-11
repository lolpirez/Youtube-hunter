import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import NicheScope
import "." as C
import "../Utils.js" as U

// AiRoutingPanel — "which AI does what" (Settings v19).
//
//   ┌ Idea generation ─────────────────────────────────────────────────┐
//   │ [💡] AI Ideas and Content Lab blueprints                          │
//   │ PROVIDER [OpenRouter ▾]   MODEL [openai/gpt-5 ▾]                  │
//   │ EFFORT   [Auto | ▂ Low | ▂▄ Med | ▂▄▆ High | ▂▄▆█ Max]            │
//   └──────────────────────────────────────────────────────────────────┘
//
// One row per feature. "Default" follows the default provider; picking a
// profile pins that feature to it (and, optionally, to one of its models), so
// e.g. ideas always come from the strong model while chat stays on a cheap one.
// Effort is per feature too; the AI Chat composer edits the same "chat" value.
ColumnLayout {
    id: root
    spacing: Tokens.space3
    Layout.fillWidth: true

    property var choices: []
    function syncChoices() {
        var c = Settings.aiRouteChoices || []
        if (JSON.stringify(c) !== JSON.stringify(choices)) choices = c
    }
    Connections { target: Settings; function onAiDataChanged() { root.syncChoices() } }
    Component.onCompleted: syncChoices()
    readonly property var choiceLabels: choices.map(function (c) { return c.label })
    readonly property var choiceIcons: choices.map(function (c) { return c.glyph })

    // v1.6: which feature rows to show ([] = all). Settings shows "ideas"
    // under AI Providers and "captions" / "dub" under Captions.
    property var features: []
    readonly property var _rows: {
        var rs = Settings.aiRoutes || []
        var out = []
        for (var i = 0; i < rs.length; i++)
            if (!root.features.length || root.features.indexOf(rs[i].feature) >= 0) out.push(i)
        return out
    }

    Repeater {
        model: root._rows.length
        delegate: Rectangle {
            id: row
            readonly property var r: (Settings.aiRoutes || [])[root._rows[index]] || ({})
            Layout.fillWidth: true
            radius: Tokens.radiusLg
            color: rowHover.hovered ? U.hoverTint(Theme.surface_low, Theme.isDark) : Theme.surface_low
            border.width: 1
            border.color: row.r.ready === false ? U.withAlpha(Theme.warn, 0.45) : Theme.border
            implicitHeight: rCol.implicitHeight + Tokens.space4 * 2
            Behavior on color { enabled: !Tokens.themeSwapping; ColorAnimation { duration: Tokens.durFast } }
            HoverHandler { id: rowHover }

            ColumnLayout {
                id: rCol
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.top: parent.top
                anchors.margins: Tokens.space4
                spacing: Tokens.space3

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Tokens.space3
                    Rectangle {
                        Layout.preferredWidth: 34; Layout.preferredHeight: 34
                        radius: 10
                        color: U.withAlpha(Theme.tertiary, Theme.isDark ? 0.16 : 0.10)
                        C.IconGlyph { anchors.centerIn: parent; name: row.r.icon || "psychology"; size: 17; glyphColor: Theme.tertiary }
                    }
                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 1
                        C.AppText { text: row.r.title || ""; weight: Tokens.weightBold; color: Theme.text_strong }
                        C.AppText { text: row.r.sub || ""; font.pixelSize: Tokens.fontCaption; color: Theme.on_surface_var; elide: Text.ElideRight; Layout.fillWidth: true }
                    }
                    // what it resolves to right now
                    Rectangle {
                        radius: Tokens.radiusPill
                        color: row.r.ready === false ? U.withAlpha(Theme.warn, 0.12) : U.withAlpha(Theme.secondary_container, 0.12)
                        implicitWidth: resRow.implicitWidth + 16
                        implicitHeight: 26
                        Layout.maximumWidth: 300
                        clip: true
                        Row {
                            id: resRow
                            anchors.centerIn: parent
                            spacing: 6
                            C.IconGlyph { anchors.verticalCenter: parent.verticalCenter; name: row.r.ready === false ? "warning" : "check_circle"; size: 13; glyphColor: row.r.ready === false ? Theme.warn : Theme.secondary_container }
                            C.AppText {
                                anchors.verticalCenter: parent.verticalCenter
                                text: row.r.ready === false ? I18n.tr("{p} is not set up").replace("{p}", row.r.resolvedName || "")
                                                            : (row.r.resolvedName || "") + " · " + (row.r.resolvedModel || "")
                                mono: row.r.ready !== false
                                font.pixelSize: Tokens.fontMicro + 1
                                color: Theme.text_strong
                                width: Math.min(implicitWidth, 260)
                                elide: Text.ElideRight
                            }
                        }
                    }
                }

                GridLayout {
                    Layout.fillWidth: true
                    columns: root.width >= 720 ? 3 : 1
                    columnSpacing: Tokens.space3
                    rowSpacing: Tokens.space3

                    ColumnLayout {
                        Layout.fillWidth: true
                        Layout.preferredWidth: 2
                        spacing: 6
                        C.AppText { text: I18n.tr("PROVIDER"); weight: Tokens.weightBold; font.pixelSize: Tokens.fontMicro; color: Theme.on_surface_var }
                        C.Dropdown {
                            Layout.fillWidth: true
                            implicitHeight: 44
                            // v1.4.8: each row offers its own list (the dub: Gemini only)
                            readonly property var opts: row.r.choices || root.choices
                            model: opts.map(function (c) { return c.label })
                            icons: opts.map(function (c) { return c.glyph })
                            currentIndex: {
                                for (var i = 0; i < opts.length; i++)
                                    if (opts[i].id === (row.r.profile || "")) return i
                                return 0
                            }
                            onActivated: Settings.setAiRoute(row.r.feature, "profile", (opts[currentIndex] || {}).id || "")
                        }
                    }
                    ColumnLayout {
                        Layout.fillWidth: true
                        Layout.preferredWidth: 2
                        spacing: 6
                        C.AppText { text: I18n.tr("MODEL"); weight: Tokens.weightBold; font.pixelSize: Tokens.fontMicro; color: Theme.on_surface_var }
                        C.Dropdown {
                            id: rModel
                            Layout.fillWidth: true
                            implicitHeight: 44
                            editable: true
                            property var stable: []
                            model: stable
                            function sync() {
                                var m = [I18n.tr("Provider's model")].concat(row.r.models || [])
                                if (JSON.stringify(m) !== JSON.stringify(stable)) {
                                    stable = m
                                    Qt.callLater(function () { if (!rModel.activeFocus) rModel.editText = row.r.model || I18n.tr("Provider's model") })
                                }
                            }
                            Connections { target: Settings; function onAiDataChanged() { rModel.sync() } }
                            Component.onCompleted: { sync(); editText = row.r.model || I18n.tr("Provider's model") }
                            function commit(t) {
                                t = String(t || "").trim()
                                Settings.setAiRoute(row.r.feature, "model", t === I18n.tr("Provider's model") ? "" : t)
                            }
                            onActivated: commit(currentIndex === 0 ? "" : stable[currentIndex])
                            onAccepted: commit(editText)
                            onActiveFocusChanged: if (!activeFocus) commit(editText)
                            Binding { target: rModel; property: "editText"; value: row.r.model || I18n.tr("Provider's model"); when: !rModel.activeFocus; restoreMode: Binding.RestoreNone }
                        }
                    }
                    ColumnLayout {
                        visible: row.r.showEffort !== false
                        Layout.fillWidth: true
                        Layout.preferredWidth: 3
                        spacing: 6
                        C.AppText { text: I18n.tr("EFFORT"); weight: Tokens.weightBold; font.pixelSize: Tokens.fontMicro; color: Theme.on_surface_var }
                        C.EffortPicker {
                            Layout.fillWidth: true
                            compact: true
                            options: Settings.effortOptions
                            value: row.r.effort || "auto"
                            onPicked: (v) => Settings.setAiRoute(row.r.feature, "effort", v)
                        }
                    }
                }

                // v1.4.13: where Captions get their words. One switch with a
                // live "flow" so the choice is seen, not just read:
                //   on : video audio → Gemini hears it → captions
                //   off: YouTube's captions → Gemini makes them fluent → captions
                Rectangle {
                    id: lis
                    visible: row.r.hasListen === true
                    readonly property bool can: row.r.listenCapable === true
                    readonly property bool on: can && row.r.listenAudio !== false
                    Layout.fillWidth: true
                    radius: Tokens.radiusMd
                    color: lis.on ? U.withAlpha(Theme.primary, Theme.isDark ? 0.07 : 0.045)
                                  : U.withAlpha(Theme.on_surface, Theme.isDark ? 0.035 : 0.025)
                    border.width: 1
                    border.color: lis.on ? U.withAlpha(Theme.primary, 0.28) : Theme.border
                    implicitHeight: lisCol.implicitHeight + Tokens.space3 * 2
                    Behavior on color { enabled: !Tokens.themeSwapping; ColorAnimation { duration: Tokens.durMed } }
                    Behavior on border.color { enabled: !Tokens.themeSwapping; ColorAnimation { duration: Tokens.durMed } }

                    ColumnLayout {
                        id: lisCol
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.top: parent.top
                        anchors.margins: Tokens.space3
                        spacing: Tokens.space3

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: Tokens.space3
                            Rectangle {
                                Layout.preferredWidth: 30; Layout.preferredHeight: 30
                                Layout.alignment: Qt.AlignTop
                                radius: 9
                                color: lis.on ? U.withAlpha(Theme.primary, Theme.isDark ? 0.18 : 0.12)
                                              : U.withAlpha(Theme.on_surface, Theme.isDark ? 0.08 : 0.06)
                                C.IconGlyph {
                                    anchors.centerIn: parent
                                    name: lis.on ? "volume_up" : "youtube"
                                    size: 15
                                    glyphColor: lis.on ? Theme.primary : Theme.on_surface_var
                                }
                            }
                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 2
                                C.AppText {
                                    text: I18n.tr("Gemini listens to the audio")
                                    weight: Tokens.weightDemiBold
                                    color: Theme.text_strong
                                }
                                C.AppText {
                                    Layout.fillWidth: true
                                    wrapMode: Text.Wrap
                                    font.pixelSize: Tokens.fontCaption
                                    color: Theme.on_surface_var
                                    text: !lis.can
                                          ? I18n.tr("Only Google's own Gemini can listen. {m} always starts from YouTube's captions and makes them fluent.")
                                                .replace("{m}", row.r.listenBrain || I18n.tr("This model"))
                                          : lis.on
                                            ? I18n.tr("On: Gemini hears the video and writes every line itself, like a human transcriber.")
                                            : I18n.tr("Off: the app takes YouTube's own captions, Gemini rewrites them fluently and translates them.")
                                }
                            }
                            C.AppSwitch {
                                Layout.alignment: Qt.AlignTop
                                enabled: lis.can
                                opacity: lis.can ? 1 : 0.45
                                checked: lis.on
                                onToggled: Settings.setCaptionsListen(checked)
                            }
                        }

                        // the flow: three steps, the first two swap with the switch
                        Flow {
                            Layout.fillWidth: true
                            spacing: 6
                            Repeater {
                                model: lis.on
                                       ? [ { icon: "volume_up", label: I18n.tr("Video audio") },
                                           { icon: "gemini",    label: I18n.tr("Gemini listens") },
                                           { icon: "subject",   label: I18n.tr("Captions") } ]
                                       : [ { icon: "youtube",   label: I18n.tr("YouTube's captions") },
                                           { icon: "gemini",    label: I18n.tr("Gemini makes them fluent") },
                                           { icon: "subject",   label: I18n.tr("Captions") } ]
                                delegate: Row {
                                    required property var modelData
                                    required property int index
                                    spacing: 6
                                    Rectangle {
                                        anchors.verticalCenter: parent.verticalCenter
                                        height: 26
                                        width: stepRow.implicitWidth + 18
                                        radius: Tokens.radiusPill
                                        color: index === 2 ? U.withAlpha(Theme.primary, Theme.isDark ? 0.16 : 0.10)
                                                           : U.withAlpha(Theme.on_surface, Theme.isDark ? 0.06 : 0.045)
                                        border.width: 1
                                        border.color: index === 2 ? U.withAlpha(Theme.primary, 0.3) : Theme.border
                                        Row {
                                            id: stepRow
                                            anchors.centerIn: parent
                                            spacing: 6
                                            C.IconGlyph {
                                                anchors.verticalCenter: parent.verticalCenter
                                                name: modelData.icon
                                                size: 13
                                                glyphColor: index === 2 ? Theme.primary : Theme.on_surface_var
                                            }
                                            C.AppText {
                                                anchors.verticalCenter: parent.verticalCenter
                                                text: modelData.label
                                                font.pixelSize: Tokens.fontCaption
                                                weight: Tokens.weightMedium
                                                color: Theme.text_strong
                                            }
                                        }
                                    }
                                    C.IconGlyph {
                                        visible: index < 2
                                        anchors.verticalCenter: parent.verticalCenter
                                        name: "arrow_forward"
                                        size: 13
                                        glyphColor: Theme.on_surface_var
                                    }
                                }
                            }
                        }

                        // the trade-off, in one line, so nobody has to guess
                        RowLayout {
                            Layout.fillWidth: true
                            spacing: Tokens.space2
                            C.IconGlyph {
                                Layout.alignment: Qt.AlignTop
                                name: lis.on ? "verified" : "bolt"
                                size: 13
                                glyphColor: Theme.on_surface_var
                            }
                            C.AppText {
                                Layout.fillWidth: true
                                wrapMode: Text.Wrap
                                font.pixelSize: Tokens.fontCaption
                                color: Theme.on_surface_var
                                text: lis.on
                                      ? I18n.tr("Best words and timing, works on any video. Uses more Gemini quota, since the audio is uploaded.")
                                      : I18n.tr("Faster and lighter on quota; quality follows YouTube's captions. Local files and videos with no captions are still heard by Gemini.")
                            }
                        }
                    }
                }

                // v1.4.10: who actually listens (captions) / the Gemini-only
                // rule (dub: Google Gemini only), in one quiet line
                Rectangle {
                    visible: !!row.r.note
                    Layout.fillWidth: true
                    radius: Tokens.radiusMd
                    color: row.r.noteWarn ? U.withAlpha(Theme.warn, 0.10) : U.withAlpha(Theme.tertiary, Theme.isDark ? 0.10 : 0.07)
                    border.width: 1
                    border.color: row.r.noteWarn ? U.withAlpha(Theme.warn, 0.35) : U.withAlpha(Theme.tertiary, 0.22)
                    implicitHeight: noteRow.implicitHeight + Tokens.space2 * 2
                    RowLayout {
                        id: noteRow
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.verticalCenter: parent.verticalCenter
                        anchors.leftMargin: Tokens.space3
                        anchors.rightMargin: Tokens.space3
                        spacing: Tokens.space2
                        C.IconGlyph {
                            Layout.alignment: Qt.AlignTop
                            name: row.r.noteWarn ? "warning" : "info"
                            size: 14
                            glyphColor: row.r.noteWarn ? Theme.warn : Theme.tertiary
                        }
                        C.AppText {
                            Layout.fillWidth: true
                            text: row.r.note || ""
                            wrapMode: Text.Wrap
                            font.pixelSize: Tokens.fontCaption
                            color: Theme.on_surface
                        }
                    }
                }

                // v1.4.8: check the pick for real before relying on it
                RowLayout {
                    visible: row.r.feature !== "ideas"
                    Layout.fillWidth: true
                    spacing: Tokens.space3
                    C.ActionButton {
                        compact: true
                        variant: "ghost"
                        iconName: "verified"
                        status: row.r.testState || 0
                        busyText: I18n.tr("Testing …")
                        text: I18n.tr("Test")
                        enabled: row.r.testState !== 1
                        onClicked: Settings.testAiRoute(row.r.feature)
                    }
                    C.AppText {
                        Layout.fillWidth: true
                        text: row.r.testMsg || ""
                        wrapMode: Text.Wrap
                        font.pixelSize: Tokens.fontCaption
                        color: row.r.testState === 3 ? Theme.warn
                             : row.r.testState === 2 ? Theme.secondary_container : Theme.on_surface_var
                        opacity: text ? 1 : 0
                        Behavior on opacity { NumberAnimation { duration: 180 } }
                    }
                }
            }
        }
    }
}
