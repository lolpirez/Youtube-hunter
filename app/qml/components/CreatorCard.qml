import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import QtQuick.Shapes
import NicheScope
import "." as C
import "../Utils.js" as U

// CreatorCard — "My channel" v2 (v1.0.6), the creator profile on AI Ideas.
//
// Design plan (check the render against it):
//   * Subject — a channel ID card, not a settings form. Collapsed, it reads
//     like a badge: a ring that fills as the AI learns the channel, the
//     format icon in the middle, and the profile as a row of chips.
//   * Order of questions (v1.0.7: easiest first, so nobody stalls on 01):
//       01 Channel  — topic / audience / what already works, in own words.
//                     Any topic fits here; there is no topic list.
//       02 Format   — what the videos LOOK like (cooking, walking tour,
//                     faceless …, or Other + own words). One pick; the
//                     biggest single lift in fit.
//       03 Stage · 04 Voice — channel size (search vs browse) and tone.
//       05 Limits   — chips + anything else, in own words.
//   * Colour — the live Theme accent marks "chosen" and the one action;
//     everything else is surface + outline, so the card sits calmly above
//     the composer and the steps below it.
//   * Every answer is saved on the spot (IdeasVM writes config.json) and is
//     sent with every run, every single-greenlight rebuild and AI Chat.
//
// Public API: `narrow`, `wide` (page breakpoints), `open`.
C.GlassPanel {
    id: card

    property bool narrow: false
    property bool wide: false
    property bool open: false

    radius: Tokens.radiusXl
    implicitHeight: col.implicitHeight + Tokens.space5 * 2
    border.color: open ? U.withAlpha(Theme.primary, 0.42)
                : Ideas.hasCreator ? U.withAlpha(Theme.primary, 0.26) : Theme.border

    readonly property int strength: Ideas.creatorStrength
    property real shownStrength: 0
    Component.onCompleted: shownStrength = strength
    onStrengthChanged: shownStrength = strength
    Behavior on shownStrength { NumberAnimation { duration: 620; easing.type: Easing.OutCubic } }

    readonly property string typeIcon: {
        var opts = Ideas.channelTypeOptions || []
        for (var i = 0; i < opts.length; i++)
            if (opts[i].key === Ideas.creatorType) return opts[i].icon
        return "user"
    }

    function flushAll() {
        nicheSave.flush()
        audienceSave.flush()
        provenSave.flush()
        typeCustomSave.flush()
        limitsCustomSave.flush()
    }

    C.CornerGlow {
        radius: card.radius
        size: 460
        strength: (Theme.isDark ? 0.10 : 0.07) + (card.open ? 0.05 : 0)
    }

    // ════════════════════════ building blocks ════════════════════════

    // A numbered question header: 01  Format · hint
    component QHead: RowLayout {
        id: qh
        property string n: ""
        property string title: ""
        property string hint: ""
        Layout.fillWidth: true
        spacing: Tokens.space3
        C.AppText {
            text: qh.n
            mono: true
            font.pixelSize: Tokens.fontCaption
            weight: Tokens.weightBold
            color: Theme.primary
            LayoutMirroring.enabled: false
            Layout.alignment: Qt.AlignBaseline
        }
        C.AppText {
            text: qh.title
            weight: Tokens.weightBold
            font.pixelSize: Tokens.fontLabel + 1
            color: Theme.text_strong
            Layout.alignment: Qt.AlignBaseline
        }
        C.AppText {
            visible: text !== ""
            text: qh.hint
            font.pixelSize: Tokens.fontCaption + 1
            color: Theme.on_surface_var
            elide: Text.ElideRight
            Layout.fillWidth: true
            Layout.alignment: Qt.AlignBaseline
        }
    }

    // A pill-shaped toggle chip (single- or multi-select is up to the host).
    component Chip: AbstractButton {
        id: chip
        property string label: ""
        property string iconName: ""
        property bool picked: false
        hoverEnabled: true
        implicitHeight: 34
        implicitWidth: chipRow.implicitWidth + 26
        HoverHandler { cursorShape: Qt.PointingHandCursor }
        background: Rectangle {
            radius: Tokens.radiusPill
            color: chip.picked ? U.withAlpha(Theme.primary, 0.14)
                 : chip.hovered ? Theme.hover_bg : Theme.surface_low
            border.width: 1
            border.color: chip.picked ? U.withAlpha(Theme.primary, 0.55) : Theme.border_med
            Behavior on color { enabled: !Tokens.themeSwapping; ColorAnimation { duration: Tokens.durFast } }
        }
        contentItem: Item {
            Row {
                id: chipRow
                anchors.centerIn: parent
                spacing: 6
                C.IconGlyph {
                    anchors.verticalCenter: parent.verticalCenter
                    name: chip.picked ? "check" : chip.iconName
                    size: 14
                    glyphColor: chip.picked ? Theme.primary : Theme.on_surface_var
                }
                C.AppText {
                    anchors.verticalCenter: parent.verticalCenter
                    text: chip.label
                    font.pixelSize: Tokens.fontCaption + 1
                    weight: Tokens.weightDemiBold
                    color: chip.picked ? Theme.text_strong : Theme.on_surface
                }
            }
        }
    }

    // A read-only summary chip on the collapsed card.
    component Tag: Rectangle {
        id: tg
        property string label: ""
        property string icon: ""
        property bool lead: false
        radius: Tokens.radiusPill
        implicitHeight: 26
        implicitWidth: tagRow.implicitWidth + 18
        color: lead ? U.withAlpha(Theme.primary, 0.13) : Theme.surface_low
        border.width: 1
        border.color: lead ? U.withAlpha(Theme.primary, 0.40) : Theme.border
        Row {
            id: tagRow
            anchors.centerIn: parent
            spacing: 5
            C.IconGlyph {
                anchors.verticalCenter: parent.verticalCenter
                name: tg.icon
                size: 13
                glyphColor: tg.lead ? Theme.primary : Theme.on_surface_var
            }
            C.AppText {
                anchors.verticalCenter: parent.verticalCenter
                text: tg.label
                font.pixelSize: Tokens.fontCaption
                weight: Tokens.weightDemiBold
                color: tg.lead ? Theme.text_strong : Theme.on_surface
            }
        }
    }

    // ══════════════════════════════ layout ══════════════════════════════
    ColumnLayout {
        id: col
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.margins: Tokens.space5
        spacing: Tokens.space5

        // ── the badge ──
        GridLayout {
            Layout.fillWidth: true
            columns: card.narrow ? 1 : 2
            columnSpacing: Tokens.space5
            rowSpacing: Tokens.space4

            RowLayout {
                Layout.fillWidth: true
                spacing: Tokens.space4

                // strength ring + format icon
                Item {
                    Layout.preferredWidth: 56
                    Layout.preferredHeight: 56
                    Layout.alignment: Qt.AlignTop
                    Shape {
                        anchors.fill: parent
                        preferredRendererType: Shape.CurveRenderer
                        ShapePath {
                            fillColor: "transparent"
                            strokeColor: U.withAlpha(Theme.on_surface, Theme.isDark ? 0.10 : 0.12)
                            strokeWidth: 3
                            PathAngleArc { centerX: 28; centerY: 28; radiusX: 26; radiusY: 26; startAngle: 0; sweepAngle: 360 }
                        }
                        ShapePath {
                            fillColor: "transparent"
                            strokeColor: Theme.primary
                            strokeWidth: 3
                            capStyle: ShapePath.RoundCap
                            PathAngleArc {
                                centerX: 28; centerY: 28; radiusX: 26; radiusY: 26
                                startAngle: -90
                                sweepAngle: 360 * Math.max(0, Math.min(1, card.shownStrength / 100))
                            }
                        }
                    }
                    C.IconTile {
                        anchors.centerIn: parent
                        iconName: card.typeIcon
                        size: 40
                        tint: Theme.primary
                        lit: Ideas.hasCreator
                    }
                }

                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: Tokens.space2

                    Row {
                        spacing: Tokens.space2
                        C.AppText {
                            anchors.verticalCenter: parent.verticalCenter
                            text: I18n.tr("My channel")
                            weight: Tokens.weightBold
                            font.pixelSize: Tokens.fontSubtitle + 1
                            color: Theme.text_strong
                        }
                        Rectangle {
                            id: statusPill
                            anchors.verticalCenter: parent.verticalCenter
                            radius: Tokens.radiusPill
                            implicitWidth: stRow.implicitWidth + 16
                            implicitHeight: 22
                            readonly property color tint: Ideas.hasCreator ? Theme.secondary_container : Theme.outline
                            color: U.withAlpha(tint, 0.12)
                            border.width: 1
                            border.color: U.withAlpha(tint, 0.35)
                            Row {
                                id: stRow
                                anchors.centerIn: parent
                                spacing: 5
                                Rectangle {
                                    width: 6; height: 6; radius: 3
                                    anchors.verticalCenter: parent.verticalCenter
                                    color: statusPill.tint
                                }
                                C.AppText {
                                    anchors.verticalCenter: parent.verticalCenter
                                    text: Ideas.hasCreator ? I18n.tr("In every run") : I18n.tr("Not set")
                                    font.pixelSize: Tokens.fontMicro
                                    weight: Tokens.weightBold
                                    color: statusPill.tint
                                }
                            }
                        }
                    }

                    // set: the niche line + the profile as chips
                    C.AppText {
                        Layout.fillWidth: true
                        visible: Ideas.hasCreator && Ideas.creatorNiche.trim() !== ""
                        text: Ideas.creatorNiche
                        font.pixelSize: Tokens.fontBody
                        color: Theme.on_surface
                        maximumLineCount: 1
                        elide: Text.ElideRight
                    }
                    Flow {
                        Layout.fillWidth: true
                        visible: Ideas.hasCreator && (Ideas.creatorChips || []).length > 0
                        spacing: Tokens.space2
                        Repeater {
                            model: Ideas.creatorChips || []
                            Tag {
                                required property var modelData
                                label: modelData.label
                                icon: modelData.icon
                                lead: modelData.kind === "type"
                            }
                        }
                    }
                    // not set: the pitch
                    C.AppText {
                        Layout.fillWidth: true
                        visible: !Ideas.hasCreator
                        text: I18n.tr("Tell the AI what you make and what you can't do, so every greenlight is one you can actually shoot.")
                        font.pixelSize: Tokens.fontCaption + 1
                        color: Theme.on_surface_var
                        wrapMode: Text.Wrap
                        maximumLineCount: 2
                        elide: Text.ElideRight
                    }
                }
            }

            // ── strength · output language · the one action ──
            RowLayout {
                Layout.alignment: card.narrow ? Qt.AlignLeft : (Qt.AlignRight | Qt.AlignTop)
                spacing: Tokens.space4

                ColumnLayout {
                    visible: !card.narrow
                    spacing: 0
                    Layout.alignment: Qt.AlignVCenter
                    C.AppText {
                        Layout.alignment: Qt.AlignRight
                        text: Math.round(card.shownStrength) + "%"
                        mono: true
                        weight: Tokens.weightBold
                        font.pixelSize: Tokens.fontSubtitle
                        color: card.strength >= 70 ? Theme.primary : Theme.text_strong
                        LayoutMirroring.enabled: false
                    }
                    C.AppText {
                        Layout.alignment: Qt.AlignRight
                        text: I18n.tr("AI knows your channel")
                        font.pixelSize: Tokens.fontMicro
                        color: Theme.outline
                    }
                }

                Rectangle {
                    visible: !card.narrow
                    Layout.preferredWidth: 1
                    Layout.preferredHeight: 30
                    color: Theme.border
                }

                RowLayout {
                    spacing: Tokens.space2
                    Layout.alignment: Qt.AlignVCenter
                    C.IconGlyph { name: "translate"; size: 15; glyphColor: Theme.on_surface_var }
                    // v1.0.7: say what the switch does (it is the language of
                    // the ideas, not of the app or of the channel)
                    C.AppText {
                        visible: !card.narrow
                        text: I18n.tr("Ideas in")
                        font.pixelSize: Tokens.fontCaption + 1
                        color: Theme.on_surface_var
                    }
                    C.SlideSegment {
                        solid: false
                        cellHeight: 30
                        fontSize: Tokens.fontCaption + 1
                        enabledCells: !Ideas.busy
                        model: Ideas.outputLanguageOptions
                        currentIndex: Ideas.outputLanguage
                        onActivated: (i) => Ideas.outputLanguage = i
                    }
                }

                C.PrimaryButton {
                    Layout.alignment: Qt.AlignVCenter
                    variant: card.open || Ideas.hasCreator ? "ghost" : "primary"
                    iconName: card.open ? "check" : (Ideas.hasCreator ? "edit" : "add")
                    text: card.open ? I18n.tr("Done")
                          : Ideas.hasCreator ? I18n.tr("Edit") : I18n.tr("Set up")
                    implicitHeight: 38
                    onClicked: {
                        if (card.open) card.flushAll()
                        card.open = !card.open
                    }
                }
            }
        }

        // ══════════════════════════ the form ══════════════════════════
        ColumnLayout {
            id: form
            visible: card.open
            Layout.fillWidth: true
            spacing: Tokens.space6
            opacity: 0
            transform: Translate { id: formShift; y: 0 }
            onVisibleChanged: if (visible) formIn.restart()
            ParallelAnimation {
                id: formIn
                NumberAnimation { target: form; property: "opacity"; from: 0; to: 1; duration: 280; easing.type: Easing.OutCubic }
                NumberAnimation { target: formShift; property: "y"; from: 10; to: 0; duration: 420; easing.type: Easing.OutCubic }
            }

            Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: Theme.border }

            // ── 01 · CHANNEL ── (first: the topic is what everyone knows)
            ColumnLayout {
                Layout.fillWidth: true
                spacing: Tokens.space3
                QHead {
                    n: "01"
                    title: I18n.tr("Channel")
                    hint: I18n.tr("Your topic, in your own words. Any topic works.")
                }
                GridLayout {
                    Layout.fillWidth: true
                    columns: card.wide ? 2 : 1
                    columnSpacing: Tokens.space4
                    rowSpacing: Tokens.space3
                    C.LineField {
                        id: nicheField
                        Layout.fillWidth: true
                        label: I18n.tr("What is your channel about?")
                        placeholder: I18n.tr("e.g. Persian home cooking in under 20 minutes")
                        Component.onCompleted: text = Ideas.creatorNiche
                        onTextChanged: nicheSave.restart()
                    }
                    C.LineField {
                        id: audienceField
                        Layout.fillWidth: true
                        label: I18n.tr("Who watches it?")
                        placeholder: I18n.tr("e.g. busy parents, 25-40, watch on their phone at night")
                        Component.onCompleted: text = Ideas.creatorAudience
                        onTextChanged: audienceSave.restart()
                    }
                }
                C.LineField {
                    id: provenField
                    Layout.fillWidth: true
                    label: I18n.tr("What already works for you?")
                    placeholder: I18n.tr("e.g. night walks get 3× my usual views · my 'fix it in 60s' Shorts")
                    Component.onCompleted: text = Ideas.creatorProven
                    onTextChanged: provenSave.restart()
                }
                // typing is saved after a short pause, not per key
                Timer {
                    id: nicheSave; interval: 700
                    onTriggered: Ideas.creatorNiche = nicheField.text
                    function flush() { if (running) { stop(); triggered() } }
                }
                Timer {
                    id: audienceSave; interval: 700
                    onTriggered: Ideas.creatorAudience = audienceField.text
                    function flush() { if (running) { stop(); triggered() } }
                }
                Timer {
                    id: provenSave; interval: 700
                    onTriggered: Ideas.creatorProven = provenField.text
                    function flush() { if (running) { stop(); triggered() } }
                }
            }

            // ── 02 · FORMAT ──
            ColumnLayout {
                Layout.fillWidth: true
                spacing: Tokens.space3
                QHead {
                    n: "02"
                    title: I18n.tr("Format")
                    hint: I18n.tr("How your videos look, not the topic. Not listed? Pick Other.")
                }
                GridLayout {
                    id: typeGrid
                    Layout.fillWidth: true
                    columns: Math.max(2, Math.min(7, Math.floor((width + columnSpacing) / (150 + columnSpacing))))
                    columnSpacing: Tokens.space2
                    rowSpacing: Tokens.space2
                    Repeater {
                        model: Ideas.channelTypeOptions || []
                        delegate: AbstractButton {
                            id: tile
                            required property var modelData
                            readonly property bool picked: Ideas.creatorType === modelData.key
                            Layout.fillWidth: true
                            Layout.preferredWidth: 1
                            hoverEnabled: true
                            implicitHeight: 92
                            onClicked: Ideas.setCreatorType(modelData.key)
                            HoverHandler { cursorShape: Qt.PointingHandCursor }
                            scale: tile.pressed ? Tokens.pressScale : 1
                            Behavior on scale { NumberAnimation { duration: Tokens.durFast; easing.type: Easing.OutCubic } }
                            background: Rectangle {
                                radius: Tokens.radiusLg
                                color: tile.picked ? U.withAlpha(Theme.primary, Theme.isDark ? 0.13 : 0.09)
                                     : tile.hovered ? Theme.hover_bg : Theme.surface_low
                                border.width: tile.picked ? 1.5 : 1
                                border.color: tile.picked ? U.withAlpha(Theme.primary, 0.65)
                                            : tile.hovered ? Theme.border_med : Theme.border
                                Behavior on color { enabled: !Tokens.themeSwapping; ColorAnimation { duration: Tokens.durFast } }
                                Rectangle {
                                    visible: tile.picked
                                    anchors.top: parent.top
                                    anchors.right: parent.right
                                    anchors.margins: 8
                                    width: 18; height: 18; radius: 9
                                    color: Theme.primary
                                    C.IconGlyph {
                                        anchors.centerIn: parent
                                        name: "check"
                                        size: 12
                                        glyphColor: Theme.on_primary
                                    }
                                }
                            }
                            contentItem: ColumnLayout {
                                spacing: 2
                                C.IconTile {
                                    iconName: tile.modelData.icon
                                    size: 30
                                    tint: tile.picked ? Theme.primary : Theme.on_surface_var
                                    lit: tile.picked
                                    Layout.preferredWidth: 30
                                    Layout.preferredHeight: 30
                                    Layout.leftMargin: Tokens.space3
                                    Layout.topMargin: Tokens.space3
                                    Layout.bottomMargin: Tokens.space1
                                }
                                C.AppText {
                                    text: tile.modelData.label
                                    weight: Tokens.weightBold
                                    font.pixelSize: Tokens.fontBody
                                    color: tile.picked ? Theme.text_strong : Theme.on_surface
                                    elide: Text.ElideRight
                                    Layout.fillWidth: true
                                    Layout.leftMargin: Tokens.space3
                                    Layout.rightMargin: Tokens.space3
                                }
                                C.AppText {
                                    text: tile.modelData.sub
                                    font.pixelSize: Tokens.fontCaption
                                    color: Theme.on_surface_var
                                    elide: Text.ElideRight
                                    Layout.fillWidth: true
                                    Layout.leftMargin: Tokens.space3
                                    Layout.rightMargin: Tokens.space3
                                    Layout.bottomMargin: Tokens.space3
                                }
                            }
                        }
                    }
                }
                // v1.0.7: "Other" — the creator's own words for the format
                C.LineField {
                    id: typeCustomField
                    Layout.fillWidth: true
                    visible: Ideas.creatorType === "other"
                    label: I18n.tr("Describe your format")
                    placeholder: I18n.tr("e.g. 2D animated bedtime stories · screen-recorded Excel tips")
                    Component.onCompleted: text = Ideas.creatorTypeCustom
                    onTextChanged: typeCustomSave.restart()
                }
                Timer {
                    id: typeCustomSave; interval: 700
                    onTriggered: Ideas.creatorTypeCustom = typeCustomField.text
                    function flush() { if (running) { stop(); triggered() } }
                }
            }

            // ── 03 · STAGE & VOICE ──
            GridLayout {
                Layout.fillWidth: true
                columns: card.wide ? 2 : 1
                columnSpacing: Tokens.space6
                rowSpacing: Tokens.space5

                ColumnLayout {
                    Layout.fillWidth: true
                    Layout.preferredWidth: 1
                    Layout.alignment: Qt.AlignTop
                    spacing: Tokens.space3
                    QHead {
                        n: "03"
                        title: I18n.tr("Stage")
                        hint: I18n.tr("Small channels win on search, big ones on browse.")
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: Tokens.space2
                        Repeater {
                            model: Ideas.stageOptions || []
                            delegate: AbstractButton {
                                id: stg
                                required property var modelData
                                readonly property bool picked: Ideas.creatorStage === modelData.key
                                Layout.fillWidth: true
                                Layout.preferredWidth: 1
                                hoverEnabled: true
                                implicitHeight: 56
                                onClicked: Ideas.setCreatorStage(modelData.key)
                                HoverHandler { cursorShape: Qt.PointingHandCursor }
                                background: Rectangle {
                                    radius: Tokens.radiusLg
                                    color: stg.picked ? U.withAlpha(Theme.primary, Theme.isDark ? 0.13 : 0.09)
                                         : stg.hovered ? Theme.hover_bg : Theme.surface_low
                                    border.width: stg.picked ? 1.5 : 1
                                    border.color: stg.picked ? U.withAlpha(Theme.primary, 0.65) : Theme.border
                                    Behavior on color { enabled: !Tokens.themeSwapping; ColorAnimation { duration: Tokens.durFast } }
                                }
                                contentItem: RowLayout {
                                    spacing: Tokens.space2
                                    C.IconGlyph {
                                        Layout.leftMargin: Tokens.space3
                                        name: stg.modelData.icon
                                        size: 16
                                        glyphColor: stg.picked ? Theme.primary : Theme.on_surface_var
                                    }
                                    ColumnLayout {
                                        Layout.fillWidth: true
                                        Layout.rightMargin: Tokens.space2
                                        spacing: 0
                                        C.AppText {
                                            text: stg.modelData.label
                                            weight: Tokens.weightBold
                                            font.pixelSize: Tokens.fontBody
                                            color: stg.picked ? Theme.text_strong : Theme.on_surface
                                            elide: Text.ElideRight
                                            Layout.fillWidth: true
                                        }
                                        C.AppText {
                                            text: stg.modelData.sub
                                            font.pixelSize: Tokens.fontCaption
                                            color: Theme.on_surface_var
                                            elide: Text.ElideRight
                                            Layout.fillWidth: true
                                        }
                                    }
                                }
                            }
                        }
                    }
                }

                ColumnLayout {
                    Layout.fillWidth: true
                    Layout.preferredWidth: 1
                    Layout.alignment: Qt.AlignTop
                    spacing: Tokens.space3
                    QHead {
                        n: "04"
                        title: I18n.tr("Voice")
                        hint: I18n.tr("How you sound on camera or in the edit.")
                    }
                    Flow {
                        Layout.fillWidth: true
                        spacing: Tokens.space2
                        Repeater {
                            model: Ideas.toneOptions || []
                            delegate: Chip {
                                required property var modelData
                                label: modelData.label
                                iconName: modelData.icon
                                picked: Ideas.creatorTone === modelData.key
                                onClicked: Ideas.setCreatorTone(modelData.key)
                            }
                        }
                    }
                }
            }

            // ── 05 · LIMITS ──
            ColumnLayout {
                Layout.fillWidth: true
                spacing: Tokens.space3
                QHead {
                    n: "05"
                    title: I18n.tr("Limits")
                    hint: I18n.tr("What you can't or won't do. Every greenlight works around them.")
                }
                Flow {
                    Layout.fillWidth: true
                    spacing: Tokens.space2
                    Repeater {
                        model: Ideas.constraintOptions || []
                        delegate: Chip {
                            required property var modelData
                            label: modelData.label
                            iconName: modelData.icon
                            picked: Ideas.creatorConstraints.indexOf(modelData.key) >= 0
                            onClicked: Ideas.toggleConstraint(modelData.key)
                        }
                    }
                }
                // v1.0.7: limits that no chip covers
                C.LineField {
                    id: limitsCustomField
                    Layout.fillWidth: true
                    label: I18n.tr("Anything else to avoid?")
                    placeholder: I18n.tr("e.g. no copyrighted music · never show my kids · one shoot day a week")
                    Component.onCompleted: text = Ideas.creatorLimitsCustom
                    onTextChanged: limitsCustomSave.restart()
                }
                Timer {
                    id: limitsCustomSave; interval: 700
                    onTriggered: Ideas.creatorLimitsCustom = limitsCustomField.text
                    function flush() { if (running) { stop(); triggered() } }
                }
            }

            // ── footer ──
            RowLayout {
                Layout.fillWidth: true
                spacing: Tokens.space3
                C.IconGlyph { name: "lock"; size: 14; glyphColor: Theme.outline }
                C.AppText {
                    Layout.fillWidth: true
                    text: I18n.tr("Saved on this computer. Sent with every greenlight, every rebuild and AI Chat.")
                    font.pixelSize: Tokens.fontCaption
                    color: Theme.outline
                    wrapMode: Text.Wrap
                }
                C.PrimaryButton {
                    visible: Ideas.hasCreator
                    variant: "ghost"
                    iconName: "restart_alt"
                    text: I18n.tr("Clear")
                    implicitHeight: 36
                    onClicked: {
                        nicheSave.stop(); audienceSave.stop(); provenSave.stop()
                        typeCustomSave.stop(); limitsCustomSave.stop()
                        Ideas.clearCreator()
                        nicheField.text = ""
                        audienceField.text = ""
                        provenField.text = ""
                        typeCustomField.text = ""
                        limitsCustomField.text = ""
                        // the cleared fields re-armed their save timers with ""
                        card.flushAll()
                    }
                }
                C.PrimaryButton {
                    variant: "primary"
                    iconName: "check"
                    text: I18n.tr("Done")
                    implicitHeight: 36
                    onClicked: { card.flushAll(); card.open = false }
                }
            }
        }
    }
}
