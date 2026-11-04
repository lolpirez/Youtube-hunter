import QtQuick
import QtQuick.Layouts
import QtQuick.Shapes
import NicheScope
import "." as C
import "../Utils.js" as U

// v1.0.4 "Signal lost" card: the scrape is paused because YouTube stopped
// answering transcript requests from this IP.
//
//   ┌──────────────────────────────────────────────────────────────────────┐
//   │ (globe·lock)  TRANSCRIPTS PAUSED                       ◔ 1:47        │
//   │               YouTube stopped answering this IP        auto-retry    │
//   │               why it happened · nothing is lost                      │
//   │ ██████████████░░░░░░▒▒▒▒   34 saved · 21 waiting · 0 no captions     │
//   │ ① Switch VPN server  ② Check new IP     [ Check new IP & resume ]    │
//   │                                   Retry now · Finish without them    │
//   └──────────────────────────────────────────────────────────────────────┘
//
// Lives at the TOP of the Scraper page (the page scrolls up to it), so the
// reason for the stop sits where the eye already is, above the composer and
// the run card. Three ways out, in the order we recommend them:
//   1. Check new IP & resume   (instant, verified: the IP must really change)
//   2. wait: the run retries by itself on the same IP after a cool-down
//   3. Finish without transcripts (honest: the export marks those rows)
// Everything tints from Theme.error / the live accent, light and dark.
Item {
    id: root

    readonly property bool shown: Dashboard.transcriptPaused && Dashboard.isRunning
    readonly property bool wide: width >= 860
    readonly property int found: Dashboard.transcriptFound
    readonly property int waiting: Dashboard.transcriptBlocked
    readonly property int none: Dashboard.transcriptNone
    readonly property int total: Math.max(1, found + waiting + none + Dashboard.transcriptSkipped)
    readonly property int cooldown: Dashboard.transcriptCooldown
    // the longest countdown seen this pause, so the ring drains from full
    property int _cdTotal: 1
    onCooldownChanged: if (cooldown > _cdTotal || !shown) _cdTotal = Math.max(1, cooldown)
    onShownChanged: {
        if (shown) { _cdTotal = Math.max(1, cooldown); arrive.restart() }
    }

    Component.onCompleted: if (shown) { _cdTotal = Math.max(1, cooldown); arrive.restart() }

    function clock(s) {
        s = Math.max(0, s | 0)
        const m = Math.floor(s / 60), r = s % 60
        return m + ":" + (r < 10 ? "0" : "") + r
    }

    visible: shown || arrive.running
    implicitHeight: shown ? card.implicitHeight : 0
    opacity: 0

    SequentialAnimation {
        id: arrive
        ParallelAnimation {
            NumberAnimation { target: root; property: "opacity"; from: 0; to: 1; duration: 360; easing.type: Easing.OutCubic }
            NumberAnimation { target: lift; property: "y"; from: -14; to: 0; duration: 520; easing.type: Easing.OutBack; easing.overshoot: 1.2 }
            NumberAnimation { target: flash; property: "t"; from: 0; to: 1; duration: 900; easing.type: Easing.OutCubic }
        }
    }
    transform: Translate { id: lift; y: 0 }

    // soft red bloom under the card: reads as "alert" without shouting
    C.GlowBlob {
        size: Math.min(root.width * 0.5, 420)
        glow: Theme.error
        strength: Theme.isDark ? 0.16 : 0.09
        x: -width * 0.25
        y: root.height / 2 - height / 2
    }

    Rectangle {
        id: card
        anchors.left: parent.left
        anchors.right: parent.right
        implicitHeight: body.implicitHeight + Tokens.space6 * 2
        radius: Tokens.radiusXl
        color: Theme.isDark ? U.withAlpha(Theme.surface_high, 0.92) : Theme.surface
        border.width: 1
        border.color: U.withAlpha(Theme.error, Theme.isDark ? 0.42 : 0.30)
        antialiasing: true
        clip: true

        // diagonal wash from the alert side
        Rectangle {
            anchors.fill: parent
            radius: parent.radius
            gradient: Gradient {
                orientation: Gradient.Horizontal
                GradientStop { position: 0.0; color: U.withAlpha(Theme.error, Theme.isDark ? 0.13 : 0.07) }
                GradientStop { position: 0.42; color: U.withAlpha(Theme.error, Theme.isDark ? 0.035 : 0.02) }
                GradientStop { position: 1.0; color: U.withAlpha(Theme.error, 0) }
            }
        }
        // lit top edge (glass catch-light)
        Rectangle {
            anchors.top: parent.top
            anchors.topMargin: 1
            anchors.horizontalCenter: parent.horizontalCenter
            width: parent.width - Tokens.radiusXl * 2
            height: 1
            gradient: Gradient {
                orientation: Gradient.Horizontal
                GradientStop { position: 0.0; color: U.withAlpha(Theme.error, 0) }
                GradientStop { position: 0.3; color: U.withAlpha(Theme.error, 0.55) }
                GradientStop { position: 1.0; color: Qt.rgba(1, 1, 1, 0) }
            }
        }

        ColumnLayout {
            id: body
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.margins: Tokens.space6
            spacing: Tokens.space5

            // ── head: orb · words · countdown ──
            RowLayout {
                Layout.fillWidth: true
                spacing: Tokens.space5

                // the orb: a globe behind a lock, with a slow radar pulse
                Item {
                    id: orb
                    Layout.alignment: Qt.AlignTop
                    implicitWidth: 64; implicitHeight: 64

                    Repeater {
                        model: 2
                        Rectangle {
                            id: ring
                            required property int index
                            anchors.centerIn: parent
                            width: 64; height: 64; radius: 32
                            color: "transparent"
                            border.width: 1.5
                            border.color: U.withAlpha(Theme.error, 0.55)
                            opacity: 0
                            antialiasing: true
                            SequentialAnimation {
                                running: root.shown && root.visible
                                loops: Animation.Infinite
                                PauseAnimation { duration: ring.index * 1100 }
                                ParallelAnimation {
                                    ScaleAnimator { target: ring; from: 0.72; to: 1.35; duration: 2200; easing.type: Easing.OutCubic }
                                    OpacityAnimator { target: ring; from: 0.8; to: 0; duration: 2200; easing.type: Easing.OutCubic }
                                }
                                PauseAnimation { duration: (1 - ring.index) * 1100 }
                            }
                        }
                    }
                    // arrival flash
                    Rectangle {
                        id: flash
                        property real t: 1
                        anchors.centerIn: parent
                        width: 64; height: 64; radius: 32
                        color: "transparent"
                        border.width: 2
                        border.color: Theme.error
                        scale: 0.6 + t * 1.2
                        opacity: (1 - t) * 0.9
                        antialiasing: true
                    }
                    Rectangle {
                        anchors.centerIn: parent
                        width: 52; height: 52; radius: 26
                        antialiasing: true
                        gradient: Gradient {
                            GradientStop { position: 0.0; color: U.withAlpha(Theme.error, Theme.isDark ? 0.30 : 0.18) }
                            GradientStop { position: 1.0; color: U.withAlpha(Theme.error, Theme.isDark ? 0.14 : 0.08) }
                        }
                        border.width: 1
                        border.color: U.withAlpha(Theme.error, 0.45)
                    }
                    C.IconGlyph {
                        anchors.centerIn: parent
                        anchors.horizontalCenterOffset: -2
                        anchors.verticalCenterOffset: -2
                        name: "public"
                        size: 24
                        glyphColor: Theme.error
                    }
                    // lock badge
                    Rectangle {
                        x: 36; y: 36
                        width: 22; height: 22; radius: 11
                        color: Theme.error
                        border.width: 2
                        border.color: Theme.isDark ? Theme.surface_high : Theme.surface
                        antialiasing: true
                        C.IconGlyph {
                            anchors.centerIn: parent
                            name: "lock"
                            fill: true
                            size: 11
                            glyphColor: "white"
                        }
                    }
                }

                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: Tokens.space1 + 2

                    // eyebrow: live dot + state
                    Row {
                        spacing: Tokens.space2
                        Rectangle {
                            id: liveDot
                            width: 7; height: 7; radius: 3.5
                            anchors.verticalCenter: parent.verticalCenter
                            color: Theme.error
                            SequentialAnimation {
                                running: root.shown && root.visible
                                loops: Animation.Infinite
                                OpacityAnimator { target: liveDot; from: 1; to: 0.25; duration: 700; easing.type: Easing.InOutSine }
                                OpacityAnimator { target: liveDot; from: 0.25; to: 1; duration: 700; easing.type: Easing.InOutSine }
                            }
                        }
                        C.AppText {
                            anchors.verticalCenter: parent.verticalCenter
                            text: Dashboard.transcriptRecovering
                                  ? I18n.tr("Second pass paused")
                                  : I18n.tr("Transcripts paused")
                            weight: Tokens.weightBold
                            font.pixelSize: Tokens.fontMicro
                            font.letterSpacing: Tokens.trackingLabel * 2
                            font.capitalization: Font.AllUppercase
                            color: Theme.error
                        }
                    }

                    C.AppText {
                        Layout.fillWidth: true
                        text: I18n.tr("YouTube stopped answering this IP")
                        weight: Tokens.weightExtraBold
                        font.pixelSize: Tokens.fontTitle
                        color: Theme.text_strong
                        wrapMode: Text.Wrap
                    }

                    C.AppText {
                        Layout.fillWidth: true
                        Layout.maximumWidth: 640
                        wrapMode: Text.Wrap
                        font.pixelSize: Tokens.fontBody
                        color: Theme.on_surface_var
                        text: (Dashboard.transcriptBlockedIp !== ""
                               ? I18n.tr("Too many caption requests came from {ip}, so YouTube is refusing them for now.")
                                     .replace("{ip}", Dashboard.transcriptBlockedIp)
                               : I18n.tr("Too many caption requests came from your connection, so YouTube is refusing them for now."))
                              + " " + I18n.tr("The run is waiting, not failing: every video so far is saved, and it resumes by itself the moment your IP changes or YouTube answers again.")
                    }
                }

                // countdown ring: the same-IP auto-retry
                Item {
                    Layout.alignment: Qt.AlignTop
                    visible: root.cooldown > 0 && root.wide
                    implicitWidth: 84; implicitHeight: 84
                    Shape {
                        anchors.fill: parent
                        preferredRendererType: Shape.CurveRenderer
                        ShapePath {
                            fillColor: "transparent"
                            strokeColor: U.withAlpha(Theme.on_surface, Theme.isDark ? 0.10 : 0.09)
                            strokeWidth: 4
                            PathAngleArc { centerX: 42; centerY: 42; radiusX: 36; radiusY: 36; startAngle: 0; sweepAngle: 360 }
                        }
                        ShapePath {
                            fillColor: "transparent"
                            strokeColor: Theme.error
                            strokeWidth: 4
                            capStyle: ShapePath.RoundCap
                            PathAngleArc {
                                centerX: 42; centerY: 42; radiusX: 36; radiusY: 36
                                startAngle: -90
                                sweepAngle: 360 * Math.max(0.002, root.cooldown / root._cdTotal)
                            }
                        }
                    }
                    Column {
                        anchors.centerIn: parent
                        spacing: 0
                        C.AppText {
                            anchors.horizontalCenter: parent.horizontalCenter
                            text: root.clock(root.cooldown)
                            mono: true
                            weight: Tokens.weightExtraBold
                            font.pixelSize: Tokens.fontLabel
                            color: Theme.text_strong
                            LayoutMirroring.enabled: false
                        }
                        C.AppText {
                            anchors.horizontalCenter: parent.horizontalCenter
                            text: I18n.tr("auto-retry")
                            font.pixelSize: Tokens.fontMicro
                            color: Theme.on_surface_var
                        }
                    }
                }
            }

            // ── coverage so far: one bar, three honest numbers ──
            ColumnLayout {
                Layout.fillWidth: true
                spacing: Tokens.space2

                Item {
                    Layout.fillWidth: true
                    implicitHeight: 8
                    Rectangle {
                        anchors.fill: parent
                        radius: 4
                        color: U.withAlpha(Theme.on_surface, Theme.isDark ? 0.08 : 0.07)
                    }
                    Row {
                        anchors.fill: parent
                        spacing: 2
                        Rectangle {
                            height: parent.height; radius: 4
                            width: Math.max(0, (parent.width - 4) * root.found / root.total)
                            visible: root.found > 0
                            color: Theme.secondary_container
                            Behavior on width { NumberAnimation { duration: Tokens.durMed; easing.type: Easing.OutCubic } }
                        }
                        Rectangle {
                            height: parent.height; radius: 4
                            width: Math.max(0, (parent.width - 4) * root.none / root.total)
                            visible: root.none > 0
                            color: U.withAlpha(Theme.outline, 0.7)
                            Behavior on width { NumberAnimation { duration: Tokens.durMed; easing.type: Easing.OutCubic } }
                        }
                        Rectangle {
                            id: waitSeg
                            height: parent.height; radius: 4
                            width: Math.max(0, (parent.width - 4) * root.waiting / root.total)
                            visible: root.waiting > 0
                            color: U.withAlpha(Theme.error, 0.85)
                            clip: true
                            Behavior on width { NumberAnimation { duration: Tokens.durMed; easing.type: Easing.OutCubic } }
                            // a glint travels the "waiting" part: queued, not lost
                            Rectangle {
                                width: 40; height: parent.height
                                gradient: Gradient {
                                    orientation: Gradient.Horizontal
                                    GradientStop { position: 0.0; color: Qt.rgba(1, 1, 1, 0) }
                                    GradientStop { position: 0.5; color: Qt.rgba(1, 1, 1, 0.45) }
                                    GradientStop { position: 1.0; color: Qt.rgba(1, 1, 1, 0) }
                                }
                                XAnimator on x {
                                    running: root.shown && root.visible && waitSeg.width > 0
                                    loops: Animation.Infinite
                                    from: -40; to: Math.max(40, waitSeg.width)
                                    duration: 1400
                                    easing.type: Easing.InOutQuad
                                }
                            }
                        }
                    }
                }

                Flow {
                    Layout.fillWidth: true
                    spacing: Tokens.space5
                    Repeater {
                        model: [
                            { n: root.found, label: I18n.tr("transcripts saved"), tint: Theme.secondary_container },
                            { n: root.waiting, label: I18n.tr("waiting for a retry"), tint: Theme.error },
                            { n: root.none, label: I18n.tr("no captions on YouTube"), tint: Theme.outline }
                        ]
                        Row {
                            required property var modelData
                            spacing: Tokens.space2
                            Rectangle {
                                width: 8; height: 8; radius: 2
                                anchors.verticalCenter: parent.verticalCenter
                                color: modelData.tint
                            }
                            C.AppText {
                                anchors.verticalCenter: parent.verticalCenter
                                text: modelData.n
                                mono: true
                                weight: Tokens.weightExtraBold
                                font.pixelSize: Tokens.fontBody
                                color: Theme.text_strong
                                LayoutMirroring.enabled: false
                            }
                            C.AppText {
                                anchors.verticalCenter: parent.verticalCenter
                                text: modelData.label
                                font.pixelSize: Tokens.fontCaption
                                color: Theme.on_surface_var
                            }
                        }
                    }
                }
            }

            // same-IP note after a check that found no change
            Rectangle {
                Layout.fillWidth: true
                visible: Dashboard.transcriptIpNote !== ""
                radius: Tokens.radiusMd
                color: U.withAlpha(Theme.warn, 0.10)
                border.width: 1
                border.color: U.withAlpha(Theme.warn, 0.40)
                implicitHeight: noteRow.implicitHeight + Tokens.space3 * 2
                RowLayout {
                    id: noteRow
                    anchors.fill: parent
                    anchors.margins: Tokens.space3
                    anchors.leftMargin: Tokens.space4
                    spacing: Tokens.space2
                    C.IconGlyph { name: "info"; fill: true; size: 15; glyphColor: Theme.warn }
                    C.AppText {
                        Layout.fillWidth: true
                        wrapMode: Text.Wrap
                        text: Dashboard.transcriptIpNote
                        weight: Tokens.weightDemiBold
                        font.pixelSize: Tokens.fontCaption + 1
                        color: Theme.warn
                    }
                }
            }

            // ── the fix: two steps, one big button, two quiet exits ──
            GridLayout {
                Layout.fillWidth: true
                columns: root.wide ? 2 : 1
                columnSpacing: Tokens.space5
                rowSpacing: Tokens.space4

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Tokens.space4
                    Repeater {
                        model: [I18n.tr("Switch your VPN server"), I18n.tr("That's it: the new IP is detected in seconds")]
                        Row {
                            required property int index
                            required property var modelData
                            spacing: Tokens.space2
                            Rectangle {
                                width: 24; height: 24; radius: 12
                                anchors.verticalCenter: parent.verticalCenter
                                color: U.withAlpha(Theme.primary, 0.12)
                                border.width: 1
                                border.color: U.withAlpha(Theme.primary, 0.35)
                                C.AppText {
                                    anchors.centerIn: parent
                                    text: index + 1
                                    mono: true
                                    weight: Tokens.weightBold
                                    font.pixelSize: Tokens.fontCaption
                                    color: Theme.primary
                                }
                            }
                            C.AppText {
                                anchors.verticalCenter: parent.verticalCenter
                                text: modelData
                                weight: Tokens.weightDemiBold
                                font.pixelSize: Tokens.fontCaption + 1
                                color: Theme.on_surface
                            }
                        }
                    }
                    Item { Layout.fillWidth: true }
                }

                ColumnLayout {
                    Layout.alignment: root.wide ? Qt.AlignRight : Qt.AlignLeft
                    spacing: Tokens.space2

                    C.PrimaryButton {
                        Layout.alignment: root.wide ? Qt.AlignRight : Qt.AlignLeft
                        implicitHeight: 48
                        variant: "primary"
                        iconName: "refresh"
                        enabled: !Dashboard.transcriptChecking
                        indeterminate: Dashboard.transcriptChecking
                        text: Dashboard.transcriptChecking ? I18n.tr("Checking your IP…")
                                                           : I18n.tr("Check new IP & resume")
                        onClicked: Dashboard.transcriptRetryIp()
                    }

                    Row {
                        Layout.alignment: root.wide ? Qt.AlignRight : Qt.AlignLeft
                        spacing: Tokens.space4

                        C.AppText {
                            id: retryNow
                            visible: root.cooldown > 0
                            text: root.wide ? I18n.tr("Retry now")
                                            : I18n.tr("Auto-retry in {t} · Retry now").replace("{t}", root.clock(root.cooldown))
                            weight: Tokens.weightBold
                            font.pixelSize: Tokens.fontCaption + 1
                            color: rnHover.hovered ? Theme.text_strong : Theme.on_surface_var
                            font.underline: rnHover.hovered
                            HoverHandler { id: rnHover; cursorShape: Qt.PointingHandCursor }
                            TapHandler { enabled: !Dashboard.transcriptChecking; onTapped: Dashboard.transcriptRetryNow() }
                        }
                        Rectangle {
                            visible: retryNow.visible
                            width: 3; height: 3; radius: 1.5
                            anchors.verticalCenter: parent.verticalCenter
                            color: Theme.outline
                        }
                        C.AppText {
                            text: I18n.tr("Finish without transcripts")
                            weight: Tokens.weightBold
                            font.pixelSize: Tokens.fontCaption + 1
                            color: fwHover.hovered ? Theme.error : Theme.on_surface_var
                            font.underline: fwHover.hovered
                            HoverHandler { id: fwHover; cursorShape: Qt.PointingHandCursor }
                            TapHandler { onTapped: Dashboard.transcriptSkipRest() }
                            C.AppTooltip {
                                visible: fwHover.hovered
                                text: I18n.tr("The rest of this run exports without transcripts. Those rows are marked \"Skipped\" or \"Blocked by YouTube\" in the file, and running the same scrape again fills them in.")
                            }
                        }
                    }
                }
            }
        }
    }
}
