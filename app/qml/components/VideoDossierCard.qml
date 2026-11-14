import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import QtQuick.Shapes
import NicheScope
import "." as C
import "../Utils.js" as U

// Video Dossier (v2.1): everything about ONE video, in one glass case file.
// v2.1.1: renamed from VideoDossier.qml. The old name shadowed the
// `VideoDossier` context property in every components/ file (a QML type
// beats a context property), so the card never opened. Do not rename back.
// Opened from any video card (Niche Finder, Archive, Scraper results) with
// VideoDossier.open({...}); the data lives in app.qml_backend.vm.dossier_vm.
//
//   ┌──────────────────────────────────────────────────────────────────┐
//   │ ◆ VIDEO DOSSIER  ● details ● comments ● transcript   [Export Excel] ✕ │
//   │ ┌────────────────┐  Title, big, two lines                          │
//   │ │  hero thumb    │  (●) Channel · 1.2M subs · 3 months ago · 12:04 │
//   │ │  (lit from     │  4.2×  ▮▮▮▮▯  STRONG OUTLIER   HD  CC  Gaming   │
//   │ └────────────────┘                                                 │
//   │  VIEWS   LIKES   COMMENTS   ENGAGEMENT   VIEWS/DAY   VS CH. AVG     │
//   │ ( Overview | Transcript | Comments 60 | Details )                  │
//   │  …                                                                 │
//   └──────────────────────────────────────────────────────────────────┘
//
// The card opens at once with what the calling card already knew (title,
// thumbnail) and fills in as each part lands. The three dots in the header
// light up one by one; Export Excel unlocks when all three are in, so the file
// is always complete.
Popup {
    id: root

    parent: Overlay.overlay
    modal: true
    dim: true
    padding: 0
    focus: true
    closePolicy: Popup.NoAutoClose          // Esc via the Shortcut below (see DonateOverlay)
    z: 40
    width: Math.min(1180, parent ? parent.width - 56 : 1000)
    height: Math.min(900, parent ? parent.height - 48 : 760)
    anchors.centerIn: parent

    Overlay.modal: C.DialogScrim {
        blurPx: 12
        dimOpacity: Theme.isDark ? 0.5 : 0.32
        frozen: true
    }

    // ── data ───────────────────────────────────────────────────────────
    readonly property var m: VideoDossier.meta
    readonly property var seed: VideoDossier.seed
    readonly property var tx: VideoDossier.transcript
    readonly property var notes: VideoDossier.notes
    readonly property bool hasMeta: m && m.videoId !== undefined
    readonly property string title: hasMeta ? m.title : (seed.title || "")
    readonly property string thumbId: hasMeta ? (m.heroThumbId || seed.thumbId || "") : (seed.thumbId || "")
    readonly property string url: hasMeta ? m.url : ("https://www.youtube.com/watch?v=" + (seed.videoId || ""))
    readonly property real outlier: hasMeta ? (m.outlier || 0) : 0
    readonly property bool wide: width >= 980

    property int tab: 0                     // 0 overview · 1 transcript · 2 comments · 3 details
    property int commentTab: 0              // 0 top · 1 latest

    // the accent and its hot twin
    readonly property color _accent: Theme.primary
    readonly property color _hot: Theme.isDark ? U.mix(_accent, "#FFFFFF", 0.42) : U.mix(_accent, "#000000", 0.08)
    readonly property bool _motion: root.opened && Tokens.appActive

    function present() { root.tab = 0; root.commentTab = 0; root.open() }
    onClosed: VideoDossier.close()

    Connections {
        target: VideoDossier
        function onToast(text, kind) { toast.show(text, kind) }
    }

    Shortcut { sequence: "Esc"; enabled: root.opened; onActivated: root.close() }
    Shortcut {
        sequence: "Ctrl+E"
        enabled: root.opened && VideoDossier.ready
        onActivated: VideoDossier.exportXlsx()
    }
    Shortcut { sequences: ["Ctrl+R", "F5"]; enabled: root.opened; onActivated: VideoDossier.reload() }

    // ── formatting ─────────────────────────────────────────────────────
    function pct(x) { return ((Number(x) || 0) * 100).toFixed(2) + "%" }
    function num(n) {
        n = Number(n) || 0
        if (n >= 1e9) return (n / 1e9).toFixed(n >= 1e10 ? 0 : 1) + "B"
        if (n >= 1e6) return (n / 1e6).toFixed(n >= 1e7 ? 0 : 1) + "M"
        if (n >= 1e3) return (n / 1e3).toFixed(n >= 1e4 ? 0 : 1) + "K"
        return String(Math.round(n))
    }
    function full(n) { return Math.round(Number(n) || 0).toLocaleString(Qt.locale("en_US"), "f", 0) }
    function mult(x) { return x > 0 ? (x >= 100 ? x.toFixed(0) : x >= 10 ? x.toFixed(1) : x.toFixed(2)) + "×" : "—" }
    // 1..5 lit bars, the same scale the cards' meters read
    function bars(x) { return x >= 10 ? 5 : x >= 5 ? 4 : x >= 2 ? 3 : x >= 1 ? 2 : x > 0 ? 1 : 0 }

    // ── entrance ───────────────────────────────────────────────────────
    property real _rise: 0
    enter: Transition {
        NumberAnimation { property: "opacity"; from: 0; to: 1; duration: 200; easing.type: Easing.OutCubic }
        NumberAnimation { property: "scale"; from: 0.955; to: 1; duration: 420; easing.type: Easing.OutBack; easing.overshoot: 1.05 }
        NumberAnimation { target: root; property: "_rise"; from: 18; to: 0; duration: 420; easing.type: Easing.OutCubic }
    }
    exit: Transition {
        NumberAnimation { property: "opacity"; from: 1; to: 0; duration: 140; easing.type: Easing.InCubic }
        NumberAnimation { property: "scale"; from: 1; to: 0.975; duration: 160; easing.type: Easing.InCubic }
    }

    // ── small parts ────────────────────────────────────────────────────
    // a frosted chip: icon + label
    // (inline components cannot see this file's ids, so each one derives the
    // accent it needs from Theme itself)
    component Chip: Rectangle {
        id: chip
        property string icon: ""
        property alias label: chipText.text
        property color tone: Theme.on_surface_var
        property bool hot: false
        readonly property color hotInk: Theme.isDark ? U.mix(Theme.primary, "#FFFFFF", 0.42) : U.mix(Theme.primary, "#000000", 0.08)
        implicitWidth: chipRow.implicitWidth + 18
        implicitHeight: 26
        radius: 13
        color: hot ? U.withAlpha(Theme.primary, Theme.isDark ? 0.18 : 0.12)
                   : U.withAlpha(Theme.on_surface, Theme.isDark ? 0.06 : 0.045)
        border.width: 1
        border.color: hot ? U.withAlpha(chip.hotInk, 0.45) : U.withAlpha(Theme.on_surface, Theme.isDark ? 0.1 : 0.08)
        Row {
            id: chipRow
            anchors.centerIn: parent
            spacing: 5
            C.IconGlyph {
                visible: chip.icon !== ""
                anchors.verticalCenter: parent.verticalCenter
                name: chip.icon
                size: 13
                glyphColor: chip.hot ? chip.hotInk : chip.tone
            }
            C.AppText {
                id: chipText
                anchors.verticalCenter: parent.verticalCenter
                font.pixelSize: Tokens.fontCaption
                weight: Tokens.weightDemiBold
                color: chip.hot ? chip.hotInk : chip.tone
            }
        }
    }

    // one stat in the strip: big number, small caps label, optional sub
    component Stat: Item {
        id: stat
        property string value: "—"
        property string label: ""
        property string sub: ""
        property bool lead: false
        Layout.fillWidth: true
        implicitHeight: statCol.implicitHeight
        Column {
            id: statCol
            spacing: 3
            width: parent.width
            C.AppText {
                text: stat.value
                mono: true
                weight: Tokens.weightBold
                font.pixelSize: stat.lead ? 24 : 21
                color: stat.lead ? (Theme.isDark ? U.mix(Theme.primary, "#FFFFFF", 0.42) : U.mix(Theme.primary, "#000000", 0.08)) : Theme.text_strong
                font.features: ({ "tnum": 1 })
                elide: Text.ElideRight
                width: parent.width
            }
            C.AppText {
                text: stat.label
                font.pixelSize: Tokens.fontMicro
                weight: Tokens.weightDemiBold
                font.capitalization: Font.AllUppercase
                font.letterSpacing: 1.4
                color: Theme.outline
                elide: Text.ElideRight
                width: parent.width
            }
            C.AppText {
                visible: text !== ""
                text: stat.sub
                font.pixelSize: Tokens.fontMicro
                color: Theme.on_surface_var
                opacity: 0.8
                elide: Text.ElideRight
                width: parent.width
            }
        }
    }

    // a section heading inside a tab: label + optional count + trailing action
    component Heading: RowLayout {
        id: hd
        property string text: ""
        property string count: ""
        property string actionIcon: ""
        property string actionTip: ""
        signal action()
        Layout.fillWidth: true
        spacing: Tokens.space2
        C.AppText {
            text: hd.text
            font.pixelSize: Tokens.fontMicro
            weight: Tokens.weightBold
            font.capitalization: Font.AllUppercase
            font.letterSpacing: 1.8
            color: Theme.on_surface_var
        }
        C.AppText {
            visible: hd.count !== ""
            text: hd.count
            mono: true
            font.pixelSize: Tokens.fontMicro
            color: Theme.isDark ? U.mix(Theme.primary, "#FFFFFF", 0.42) : U.mix(Theme.primary, "#000000", 0.08)
        }
        Item { Layout.fillWidth: true }
        C.IconButton {
            visible: hd.actionIcon !== ""
            implicitWidth: 30; implicitHeight: 30
            glyphSize: 15
            iconName: hd.actionIcon
            tip: hd.actionTip
            onClicked: hd.action()
        }
    }

    // loading stand-in: a soft bar that breathes
    component Skeleton: Rectangle {
        id: sk
        implicitHeight: 14
        radius: 7
        color: U.withAlpha(Theme.on_surface, Theme.isDark ? 0.07 : 0.06)
        SequentialAnimation on opacity {
            running: sk.visible && Tokens.appActive
            loops: Animation.Infinite
            NumberAnimation { to: 0.45; duration: 700; easing.type: Easing.InOutSine }
            NumberAnimation { to: 1; duration: 700; easing.type: Easing.InOutSine }
        }
    }

    // the "collecting" dots in the header
    component Part: Row {
        id: part
        property string label: ""
        property bool loading: true
        readonly property color hotInk: Theme.isDark ? U.mix(Theme.primary, "#FFFFFF", 0.42) : U.mix(Theme.primary, "#000000", 0.08)
        spacing: 6
        Item {
            anchors.verticalCenter: parent.verticalCenter
            width: 8; height: 8
            Rectangle {
                anchors.fill: parent
                radius: 4
                antialiasing: true
                color: part.loading ? U.withAlpha(Theme.on_surface, 0.22) : part.hotInk
                Behavior on color { ColorAnimation { duration: 260 } }
            }
            Rectangle {
                // a ping ring while this part is still on its way
                id: ping
                anchors.centerIn: parent
                width: 8; height: 8; radius: 4
                color: "transparent"
                border.width: 1
                border.color: part.hotInk
                visible: part.loading && part.visible && Tokens.appActive
                SequentialAnimation on scale {
                    running: ping.visible
                    loops: Animation.Infinite
                    NumberAnimation { from: 1; to: 2.4; duration: 1100; easing.type: Easing.OutCubic }
                }
                SequentialAnimation on opacity {
                    running: ping.visible
                    loops: Animation.Infinite
                    NumberAnimation { from: 0.8; to: 0; duration: 1100; easing.type: Easing.OutCubic }
                }
            }
        }
        C.AppText {
            anchors.verticalCenter: parent.verticalCenter
            text: part.label
            font.pixelSize: Tokens.fontCaption
            weight: Tokens.weightMedium
            color: part.loading ? Theme.outline : Theme.on_surface_var
        }
    }

    // ── the card ───────────────────────────────────────────────────────
    background: Item {
        Rectangle {
            id: cardBg
            anchors.fill: parent
            radius: Tokens.radiusXl
            color: Theme.isDark ? U.mix(Theme.surface, "#000000", 0.22) : Theme.surface
            border.width: 1
            border.color: U.withAlpha(Theme.on_surface, Theme.isDark ? 0.09 : 0.1)
        }
        // light from the thumbnail: the accent, spilling out behind the hero
        Item {
            anchors.fill: parent
            anchors.margins: 1
            clip: true
            C.GlowBlob {
                size: 520
                spread: 1.8
                glow: root._accent
                strength: Theme.isDark ? 0.14 : 0.07
                x: -160
                y: -220
            }
            C.GlowBlob {
                size: 420
                spread: 1.6
                glow: U.mix(root._accent, "#ff3d6e", 0.45)
                strength: Theme.isDark ? 0.06 : 0.03
                x: parent.width - 280
                y: -200
            }
        }
        // specular top edge, glass
        Rectangle {
            anchors.top: parent.top
            anchors.topMargin: 1
            anchors.horizontalCenter: parent.horizontalCenter
            width: parent.width - Tokens.radiusXl * 2
            height: 1
            gradient: Gradient {
                orientation: Gradient.Horizontal
                GradientStop { position: 0.0; color: U.withAlpha("#FFFFFF", 0.0) }
                GradientStop { position: 0.5; color: U.withAlpha("#FFFFFF", Theme.isDark ? 0.16 : 0.9) }
                GradientStop { position: 1.0; color: U.withAlpha("#FFFFFF", 0.0) }
            }
        }
    }

    contentItem: Item {
        transform: Translate { y: root._rise }

        ColumnLayout {
            anchors.fill: parent
            anchors.margins: Tokens.space6
            anchors.topMargin: Tokens.space5
            spacing: Tokens.space5

            // ── header bar ──
            RowLayout {
                Layout.fillWidth: true
                spacing: Tokens.space4

                Row {
                    spacing: 8
                    Rectangle {
                        anchors.verticalCenter: parent.verticalCenter
                        width: 22; height: 22; radius: 7
                        antialiasing: true
                        gradient: Gradient {
                            GradientStop { position: 0; color: root._hot }
                            GradientStop { position: 1; color: root._accent }
                        }
                        C.IconGlyph {
                            anchors.centerIn: parent
                            name: "page_insights"
                            size: 13
                            glyphColor: Theme.on_primary
                        }
                    }
                    C.AppText {
                        anchors.verticalCenter: parent.verticalCenter
                        text: I18n.tr("Video dossier")
                        font.pixelSize: Tokens.fontCaption
                        weight: Tokens.weightBold
                        font.capitalization: Font.AllUppercase
                        font.letterSpacing: 2.2
                        color: Theme.text_strong
                    }
                }

                Rectangle { width: 1; height: 18; color: U.withAlpha(Theme.on_surface, 0.12); visible: root.wide }

                Row {
                    visible: root.wide
                    spacing: Tokens.space4
                    Part { label: I18n.tr("Details"); loading: VideoDossier.metaLoading }
                    Part { label: I18n.tr("Comments"); loading: VideoDossier.commentsLoading }
                    Part { label: I18n.tr("Transcript"); loading: VideoDossier.transcriptLoading }
                }

                Item { Layout.fillWidth: true }

                C.IconButton {
                    implicitWidth: 38; implicitHeight: 38
                    iconName: "open_in_new"
                    tip: I18n.tr("Open on YouTube")
                    onClicked: VideoDossier.openUrl(root.url)
                }
                C.IconButton {
                    implicitWidth: 38; implicitHeight: 38
                    iconName: "link"
                    tip: I18n.tr("Copy the link")
                    onClicked: VideoDossier.copyText(root.url)
                }

                // the export: locked until all three parts are in, then one
                // click; once saved it turns into "Show in folder"
                C.PrimaryButton {
                    id: exportBtn
                    implicitHeight: 40
                    readonly property string st: VideoDossier.exportState
                    // all three parts are in but the video itself could not be read
                    readonly property bool failed: VideoDossier.partsDone === 3 && !VideoDossier.ready
                    variant: st === "saved" || failed ? "ghost" : "primary"
                    enabled: st === "saved" || failed || (VideoDossier.ready && st !== "saving")
                    iconName: st === "saved" ? "folder_open"
                            : st === "saving" ? "downloading"
                            : VideoDossier.ready ? "download" : failed ? "refresh" : "hourglass_empty"
                    text: st === "saved" ? I18n.tr("Show in folder")
                        : st === "saving" ? I18n.tr("Saving…")
                        : VideoDossier.ready ? I18n.tr("Export Excel")
                        : failed ? I18n.tr("Try again")
                        : I18n.tr("Collecting {n}/3").replace("{n}", VideoDossier.partsDone)
                    progress: (VideoDossier.ready || failed) ? -1 : VideoDossier.partsDone / 3
                    indeterminate: st === "saving"
                    onClicked: st === "saved" ? VideoDossier.revealExport()
                             : failed ? VideoDossier.reload() : VideoDossier.exportXlsx()
                    C.AppTooltip {
                        visible: exportBtn.hovered
                        text: exportBtn.st === "saved" ? VideoDossier.exportPath
                              : I18n.tr("Every field, the full transcript and all comments in one Excel file  (Ctrl+E)")
                    }
                }
                C.IconButton {
                    implicitWidth: 38; implicitHeight: 38
                    iconName: "close"
                    tip: I18n.tr("Close  (Esc)")
                    onClicked: root.close()
                }
            }

            // ── hero: thumbnail + identity ──
            RowLayout {
                Layout.fillWidth: true
                spacing: Tokens.space6

                // the thumbnail, lit from behind
                Item {
                    id: heroBox
                    Layout.preferredWidth: root.wide ? 384 : 300
                    Layout.preferredHeight: Math.round(Layout.preferredWidth * 9 / 16)
                    Layout.alignment: Qt.AlignTop

                    C.GlowBlob {
                        anchors.centerIn: parent
                        size: parent.width * 0.9
                        spread: 1.2
                        glow: root._accent
                        strength: Theme.isDark ? 0.22 : 0.1
                    }
                    Rectangle {
                        id: heroFrame
                        anchors.fill: parent
                        radius: Tokens.radiusLg
                        color: U.withAlpha(Theme.on_surface, 0.06)
                        clip: true
                        Image {
                            id: hero
                            anchors.fill: parent
                            source: root.thumbId !== "" ? "image://thumbs/" + root.thumbId : ""
                            sourceSize.width: 768
                            sourceSize.height: 432
                            fillMode: Image.PreserveAspectCrop
                            asynchronous: true
                            cache: true
                            opacity: status === Image.Ready ? 1 : 0
                            Behavior on opacity { NumberAnimation { duration: 260 } }
                        }
                        // a scan line while the details load
                        Rectangle {
                            visible: VideoDossier.metaLoading && root._motion
                            width: parent.width
                            height: 46
                            gradient: Gradient {
                                GradientStop { position: 0; color: U.withAlpha(root._hot, 0.0) }
                                GradientStop { position: 0.5; color: U.withAlpha(root._hot, 0.22) }
                                GradientStop { position: 1; color: U.withAlpha(root._hot, 0.0) }
                            }
                            NumberAnimation on y {
                                running: parent.visible
                                loops: Animation.Infinite
                                from: -46; to: heroFrame.height
                                duration: 1300
                                easing.type: Easing.InOutSine
                            }
                        }
                        // duration + short badge
                        Rectangle {
                            visible: root.hasMeta && (m.durationS || 0) > 0
                            anchors.right: parent.right
                            anchors.bottom: parent.bottom
                            anchors.margins: 10
                            width: durText.implicitWidth + 14
                            height: 22
                            radius: 6
                            color: U.withAlpha("#000000", 0.72)
                            C.AppText {
                                id: durText
                                anchors.centerIn: parent
                                text: root.hasMeta ? ((m.isShort ? "SHORT · " : "") + m.durationText) : ""
                                mono: true
                                font.pixelSize: Tokens.fontMicro
                                weight: Tokens.weightBold
                                color: "#ffffff"
                            }
                        }
                        MouseArea {
                            anchors.fill: parent
                            cursorShape: Qt.PointingHandCursor
                            onClicked: VideoDossier.openUrl(root.url)
                        }
                    }
                    // glass rim
                    Rectangle {
                        anchors.fill: parent
                        radius: Tokens.radiusLg
                        color: "transparent"
                        border.width: 1
                        border.color: U.withAlpha("#FFFFFF", Theme.isDark ? 0.12 : 0.5)
                    }
                }

                ColumnLayout {
                    Layout.fillWidth: true
                    Layout.alignment: Qt.AlignTop
                    spacing: Tokens.space3

                    C.AppText {
                        Layout.fillWidth: true
                        text: root.title
                        selectable: true
                        wrapMode: Text.WordWrap
                        maximumLineCount: 3
                        elide: Text.ElideRight
                        font.pixelSize: root.wide ? 26 : 22
                        weight: Tokens.weightBold
                        lineHeight: 1.12
                        color: Theme.text_strong
                    }

                    // channel line
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: Tokens.space2
                        Rectangle {
                            Layout.preferredWidth: 26; Layout.preferredHeight: 26
                            radius: 13
                            color: U.withAlpha(root._accent, 0.25)
                            clip: true
                            C.AppText {
                                anchors.centerIn: parent
                                visible: chanImg.status !== Image.Ready
                                text: (root.hasMeta ? m.channelTitle : (seed.channelTitle || "?")).substring(0, 1).toUpperCase()
                                weight: Tokens.weightBold
                                font.pixelSize: Tokens.fontCaption
                                color: root._hot
                            }
                            Image {
                                id: chanImg
                                anchors.fill: parent
                                source: root.hasMeta && m.channelThumbId ? "image://thumbs/circle/" + m.channelThumbId : ""
                                sourceSize.width: 52; sourceSize.height: 52
                                asynchronous: true
                            }
                        }
                        C.AppText {
                            text: root.hasMeta ? m.channelTitle : (seed.channelTitle || "")
                            weight: Tokens.weightDemiBold
                            font.pixelSize: Tokens.fontLabel
                            color: Theme.on_surface
                            elide: Text.ElideRight
                            Layout.maximumWidth: 280
                            MouseArea {
                                anchors.fill: parent
                                enabled: root.hasMeta && m.channelUrl !== ""
                                cursorShape: Qt.PointingHandCursor
                                onClicked: VideoDossier.openUrl(m.channelUrl)
                            }
                        }
                        C.AppText {
                            Layout.fillWidth: true
                            visible: root.hasMeta
                            text: root.hasMeta
                                  ? ["", (m.subsHidden ? I18n.tr("subs hidden") : m.subsText + " " + I18n.tr("subs")),
                                     m.ageText, m.publishedText].filter(function (s) { return s !== "" }).join("   ·   ")
                                  : ""
                            font.pixelSize: Tokens.fontCaption
                            color: Theme.outline
                            elide: Text.ElideRight
                        }
                    }

                    Skeleton { visible: VideoDossier.metaLoading; Layout.preferredWidth: 260 }

                    // the outlier, the number this app is about
                    RowLayout {
                        visible: root.hasMeta
                        Layout.topMargin: Tokens.space1
                        spacing: Tokens.space4
                        C.AppText {
                            text: root.mult(root.outlier)
                            mono: true
                            weight: Tokens.weightExtraBold
                            font.pixelSize: 40
                            font.letterSpacing: -1
                            color: root.outlier >= 2 ? root._hot : Theme.text_strong
                            font.features: ({ "tnum": 1 })
                        }
                        ColumnLayout {
                            spacing: 5
                            // level meter, the cards' language
                            Row {
                                spacing: 3
                                Repeater {
                                    model: 5
                                    Rectangle {
                                        required property int index
                                        width: 16; height: 6; radius: 3
                                        antialiasing: true
                                        color: index < root.bars(root.outlier)
                                               ? U.mix(root._accent, root._hot, index / 4)
                                               : U.withAlpha(Theme.on_surface, 0.1)
                                    }
                                }
                            }
                            C.AppText {
                                text: (root.hasMeta && m.tier ? m.tier : I18n.tr("Outlier")) + "  ·  " + I18n.tr("views per subscriber")
                                font.pixelSize: Tokens.fontMicro
                                weight: Tokens.weightDemiBold
                                font.capitalization: Font.AllUppercase
                                font.letterSpacing: 1.2
                                color: Theme.on_surface_var
                            }
                        }
                        // the calling card's own score, when it had another one
                        // (Scraper: against the videos around it)
                        Chip {
                            visible: (seed.outlierText || "") !== ""
                            icon: "trending_up"
                            hot: true
                            label: seed.outlierText + "  " + (seed.outlierLabel || "")
                        }
                    }

                    Flow {
                        visible: root.hasMeta
                        Layout.fillWidth: true
                        spacing: 6
                        Chip { visible: root.hasMeta && m.definition !== ""; icon: "hd"; label: root.hasMeta ? m.definition : "" }
                        Chip { visible: root.hasMeta && m.hasCaptions === true; icon: "subject"; label: I18n.tr("Captions") }
                        Chip { visible: root.hasMeta && m.isShort === true; icon: "bolt"; label: I18n.tr("Short") }
                        Chip { visible: root.hasMeta && m.category !== ""; icon: "category"; label: root.hasMeta ? m.category : "" }
                        Chip { visible: root.hasMeta && m.language !== ""; icon: "translate"; label: root.hasMeta ? m.language.toUpperCase() : "" }
                        Chip { visible: root.hasMeta && m.madeForKids === true; icon: "favorite"; label: I18n.tr("Made for kids") }
                        Chip { visible: root.hasMeta && m.liveContent !== "none" && m.liveContent !== ""; icon: "smart_display"; label: root.hasMeta ? m.liveContent : "" }
                        Chip {
                            visible: root.hasMeta
                            icon: root.hasMeta && m.source === "api" ? "verified" : "terminal"
                            tone: Theme.outline
                            label: root.hasMeta && m.source === "api" ? I18n.tr("YouTube API") : I18n.tr("yt-dlp")
                        }
                    }
                }
            }

            // ── stat strip ──
            Rectangle {
                Layout.fillWidth: true
                implicitHeight: strip.implicitHeight + Tokens.space4 * 2
                radius: Tokens.radiusLg
                color: U.withAlpha(Theme.on_surface, Theme.isDark ? 0.035 : 0.025)
                border.width: 1
                border.color: U.withAlpha(Theme.on_surface, Theme.isDark ? 0.07 : 0.06)

                RowLayout {
                    id: strip
                    anchors.fill: parent
                    anchors.margins: Tokens.space4
                    anchors.leftMargin: Tokens.space5
                    anchors.rightMargin: Tokens.space5
                    spacing: Tokens.space5
                    visible: root.hasMeta
                    Stat { value: root.hasMeta ? m.viewsText : "—"; label: I18n.tr("Views"); sub: root.hasMeta ? root.full(m.views) : "" }
                    Stat { value: root.hasMeta ? (m.likesHidden ? "—" : m.likesText) : "—"; label: I18n.tr("Likes"); sub: root.hasMeta ? root.pct(m.likeRate) + " " + I18n.tr("of views") : "" }
                    Stat { value: root.hasMeta ? (m.commentsHidden ? "—" : m.commentsText) : "—"; label: I18n.tr("Comments"); sub: root.hasMeta ? root.pct(m.commentRate) + " " + I18n.tr("of views") : "" }
                    Stat { lead: true; value: root.hasMeta ? root.pct(m.engagement) : "—"; label: I18n.tr("Engagement"); sub: I18n.tr("likes + comments / views") }
                    Stat { value: root.hasMeta ? root.num(m.viewsPerDay) : "—"; label: I18n.tr("Views / day"); sub: root.hasMeta ? Math.round(m.ageDays) + " " + I18n.tr("days live") : "" }
                    Stat { value: root.hasMeta ? root.mult(m.vsChannelAvg) : "—"; label: I18n.tr("Vs channel avg"); sub: root.hasMeta && m.channelAvgViews > 0 ? I18n.tr("avg") + " " + root.num(m.channelAvgViews) : "" }
                }
                RowLayout {
                    anchors.fill: parent
                    anchors.margins: Tokens.space5
                    visible: !root.hasMeta
                    spacing: Tokens.space6
                    Repeater {
                        model: 6
                        Skeleton { Layout.fillWidth: true; implicitHeight: 28; radius: 8 }
                    }
                }
            }

            // notes: no key / quota / fallbacks / errors, quiet and honest
            C.AppText {
                Layout.fillWidth: true
                visible: text !== ""
                text: [root.notes.meta || "", root.notes.source || ""].filter(function (s) { return s !== "" }).join("   ·   ")
                font.pixelSize: Tokens.fontCaption
                color: root.notes.meta ? Theme.error : Theme.outline
                wrapMode: Text.WordWrap
            }

            // ── tabs: a glass segmented control with a sliding light ──
            Item {
                id: tabsBox
                Layout.fillWidth: true
                implicitHeight: 40
                readonly property var labels: [
                    I18n.tr("Overview"),
                    I18n.tr("Transcript") + (root.tx.available ? "  " + root.num(root.tx.words) : ""),
                    I18n.tr("Comments") + "  " + (VideoDossier.topComments.length + VideoDossier.latestComments.length),
                    I18n.tr("Details")
                ]
                readonly property var icons: ["article", "subject", "messages", "data"]
                Rectangle {
                    id: tabsBg
                    width: tabsRow.implicitWidth + 8
                    height: 40
                    radius: 20
                    color: U.withAlpha(Theme.on_surface, Theme.isDark ? 0.05 : 0.04)
                    border.width: 1
                    border.color: U.withAlpha(Theme.on_surface, Theme.isDark ? 0.08 : 0.07)
                    // the light under the active tab
                    Rectangle {
                        id: tabLight
                        readonly property Item cur: tabsRep.count > root.tab ? tabsRep.itemAt(root.tab) : null
                        x: cur ? cur.x + 4 : 4
                        y: 4
                        width: cur ? cur.width : 0
                        height: 32
                        radius: 16
                        gradient: Gradient {
                            orientation: Gradient.Horizontal
                            GradientStop { position: 0; color: U.withAlpha(root._accent, Theme.isDark ? 0.34 : 0.2) }
                            GradientStop { position: 1; color: U.withAlpha(root._hot, Theme.isDark ? 0.22 : 0.14) }
                        }
                        border.width: 1
                        border.color: U.withAlpha(root._hot, 0.4)
                        Behavior on x { NumberAnimation { duration: 320; easing.type: Easing.OutBack; easing.overshoot: 0.9 } }
                        Behavior on width { NumberAnimation { duration: 320; easing.type: Easing.OutCubic } }
                    }
                    Row {
                        id: tabsRow
                        x: 4; y: 4
                        Repeater {
                            id: tabsRep
                            model: 4
                            Item {
                                required property int index
                                width: tabInner.implicitWidth + 30
                                height: 32
                                Row {
                                    id: tabInner
                                    anchors.centerIn: parent
                                    spacing: 7
                                    C.IconGlyph {
                                        anchors.verticalCenter: parent.verticalCenter
                                        name: tabsBox.icons[index]
                                        size: 15
                                        glyphColor: root.tab === index ? root._hot : Theme.on_surface_var
                                    }
                                    C.AppText {
                                        anchors.verticalCenter: parent.verticalCenter
                                        text: tabsBox.labels[index]
                                        font.pixelSize: Tokens.fontBody
                                        weight: root.tab === index ? Tokens.weightBold : Tokens.weightMedium
                                        color: root.tab === index ? Theme.text_strong : Theme.on_surface_var
                                    }
                                }
                                MouseArea {
                                    anchors.fill: parent
                                    cursorShape: Qt.PointingHandCursor
                                    onClicked: root.tab = index
                                }
                            }
                        }
                    }
                }
            }

            // ── tab bodies ──
            StackLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                currentIndex: root.tab

                // 0 · OVERVIEW: description, tags, thumbnails, links
                C.SmoothScrollView {
                    id: ovScroll
                    ColumnLayout {
                        width: ovScroll.availableWidth
                        spacing: Tokens.space5

                        Heading {
                            text: I18n.tr("Description")
                            count: root.hasMeta ? root.full((m.description || "").length) + " " + I18n.tr("chars") : ""
                            actionIcon: "content_copy"
                            actionTip: I18n.tr("Copy the description")
                            onAction: VideoDossier.copyText(root.hasMeta ? m.description : "")
                        }
                        C.AppText {
                            Layout.fillWidth: true
                            visible: root.hasMeta
                            text: root.hasMeta ? (m.description || I18n.tr("No description.")) : ""
                            selectable: true
                            wrapMode: Text.Wrap
                            font.pixelSize: Tokens.fontBody
                            lineHeight: 1.35
                            color: Theme.on_surface
                        }
                        Repeater {
                            model: root.hasMeta ? 0 : 4
                            Skeleton { Layout.fillWidth: true; Layout.rightMargin: index === 3 ? 240 : 0 }
                        }

                        Heading {
                            Layout.topMargin: Tokens.space2
                            text: I18n.tr("Tags")
                            count: root.hasMeta ? String((m.tags || []).length) : ""
                            actionIcon: root.hasMeta && (m.tags || []).length ? "content_copy" : ""
                            actionTip: I18n.tr("Copy all tags")
                            onAction: VideoDossier.copyText(m.tagsText)
                        }
                        Flow {
                            Layout.fillWidth: true
                            spacing: 6
                            Repeater {
                                model: root.hasMeta ? (m.tags || []) : []
                                Chip {
                                    required property var modelData
                                    label: "#" + modelData
                                    MouseArea {
                                        anchors.fill: parent
                                        cursorShape: Qt.PointingHandCursor
                                        onClicked: VideoDossier.copyText(parent.modelData)
                                    }
                                }
                            }
                        }
                        C.AppText {
                            visible: root.hasMeta && (m.tags || []).length === 0
                            text: I18n.tr("This video has no tags.")
                            color: Theme.outline
                            font.pixelSize: Tokens.fontCaption
                        }

                        Heading {
                            Layout.topMargin: Tokens.space2
                            text: I18n.tr("Thumbnails")
                            count: root.hasMeta ? String((m.thumbnails || []).length) : ""
                        }
                        Flow {
                            Layout.fillWidth: true
                            spacing: Tokens.space3
                            Repeater {
                                model: root.hasMeta ? (m.thumbnails || []) : []
                                Rectangle {
                                    required property var modelData
                                    width: 168; height: 128
                                    radius: Tokens.radiusMd
                                    color: U.withAlpha(Theme.on_surface, Theme.isDark ? 0.04 : 0.03)
                                    border.width: 1
                                    border.color: thumbHover.hovered ? U.withAlpha(root._hot, 0.6)
                                                                     : U.withAlpha(Theme.on_surface, 0.08)
                                    HoverHandler { id: thumbHover; cursorShape: Qt.PointingHandCursor }
                                    Column {
                                        anchors.fill: parent
                                        anchors.margins: 6
                                        spacing: 6
                                        Rectangle {
                                            width: parent.width; height: 88
                                            radius: Tokens.radiusSm
                                            clip: true
                                            color: U.withAlpha(Theme.on_surface, 0.05)
                                            Image {
                                                anchors.fill: parent
                                                source: "image://thumbs/" + Qt.btoa(modelData.url)
                                                sourceSize.width: 320; sourceSize.height: 180
                                                fillMode: Image.PreserveAspectCrop
                                                asynchronous: true
                                            }
                                        }
                                        Row {
                                            spacing: 6
                                            C.AppText {
                                                text: modelData.label
                                                weight: Tokens.weightBold
                                                font.pixelSize: Tokens.fontCaption
                                                color: Theme.text_strong
                                            }
                                            C.AppText {
                                                text: modelData.w > 0 ? modelData.w + "×" + modelData.h : ""
                                                mono: true
                                                font.pixelSize: Tokens.fontMicro
                                                color: Theme.outline
                                                anchors.verticalCenter: parent.verticalCenter
                                            }
                                        }
                                    }
                                    MouseArea {
                                        anchors.fill: parent
                                        cursorShape: Qt.PointingHandCursor
                                        onClicked: VideoDossier.openUrl(parent.modelData.url)
                                    }
                                    C.IconButton {
                                        anchors.right: parent.right
                                        anchors.top: parent.top
                                        anchors.margins: 10
                                        visible: thumbHover.hovered
                                        overlay: true
                                        implicitWidth: 28; implicitHeight: 28
                                        glyphSize: 14
                                        iconName: "content_copy"
                                        accent: "#ffffff"
                                        tip: I18n.tr("Copy the image link")
                                        onClicked: VideoDossier.copyText(modelData.url)
                                    }
                                }
                            }
                        }
                        Item { implicitHeight: Tokens.space4 }
                    }
                }

                // 1 · TRANSCRIPT: the whole track, plain or with timestamps (v1.4)
                ColumnLayout {
                    id: txPane
                    spacing: Tokens.space3
                    property bool stamped: false            // false plain · true with timestamps
                    readonly property bool timedReady: VideoDossier.timedState === "ready"
                    readonly property bool timedBusy: VideoDossier.timedState === "loading"
                    readonly property string vidId: root.seed.videoId || ""
                    function setStamped(v) {
                        stamped = v
                        if (v) VideoDossier.loadTimed()
                    }
                    // another video opened in the same card: read ITS timing
                    onVidIdChanged: if (stamped) VideoDossier.loadTimed()

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: Tokens.space3
                        Chip {
                            visible: !VideoDossier.transcriptLoading
                            icon: root.tx.available ? "check_circle" : "info"
                            hot: root.tx.available === true
                            label: root.tx.available
                                   ? (root.tx.status || "") + "  ·  " + root.full(root.tx.words) + " " + I18n.tr("words") + "  ·  " + root.full(root.tx.chars) + " " + I18n.tr("chars")
                                   : I18n.tr("No captions found for this video")
                        }
                        C.AppText {
                            visible: VideoDossier.transcriptLoading
                            text: I18n.tr("Reading the whole caption track…")
                            color: Theme.outline
                            font.pixelSize: Tokens.fontCaption
                        }

                        // Plain text | With timestamps
                        Rectangle {
                            id: txSeg
                            visible: root.tx.available === true
                            implicitWidth: segRow.implicitWidth + 8
                            implicitHeight: 34
                            radius: 17
                            color: U.withAlpha(Theme.on_surface, Theme.isDark ? 0.05 : 0.04)
                            border.width: 1
                            border.color: U.withAlpha(Theme.on_surface, Theme.isDark ? 0.08 : 0.07)
                            Row {
                                id: segRow
                                x: 4; y: 3
                                spacing: 2
                                Repeater {
                                    model: [
                                        { t: I18n.tr("Plain text"), i: "subject", st: false },
                                        { t: I18n.tr("With timestamps"), i: "schedule", st: true }
                                    ]
                                    Rectangle {
                                        required property var modelData
                                        readonly property bool on: txPane.stamped === modelData.st
                                        implicitWidth: segItem.implicitWidth + 24
                                        implicitHeight: 28
                                        radius: 14
                                        color: on ? U.withAlpha(root._accent, Theme.isDark ? 0.26 : 0.16) : "transparent"
                                        border.width: on ? 1 : 0
                                        border.color: U.withAlpha(root._hot, 0.45)
                                        Behavior on color { ColorAnimation { duration: 160 } }
                                        Row {
                                            id: segItem
                                            anchors.centerIn: parent
                                            spacing: 6
                                            C.IconGlyph { anchors.verticalCenter: parent.verticalCenter; name: modelData.i; size: 14; glyphColor: parent.parent.on ? root._hot : Theme.on_surface_var }
                                            C.AppText {
                                                anchors.verticalCenter: parent.verticalCenter
                                                text: modelData.t
                                                weight: Tokens.weightDemiBold
                                                font.pixelSize: Tokens.fontCaption + 1
                                                color: parent.parent.on ? Theme.text_strong : Theme.on_surface_var
                                            }
                                        }
                                        MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: txPane.setStamped(modelData.st) }
                                    }
                                }
                            }
                        }

                        Item { Layout.fillWidth: true }
                        C.PrimaryButton {
                            visible: root.tx.available === true && txPane.stamped && txPane.timedReady
                            variant: "ghost"
                            implicitHeight: 34
                            iconName: "download"
                            text: I18n.tr("Save .srt")
                            onClicked: VideoDossier.saveTimedFile("srt")
                        }
                        C.PrimaryButton {
                            visible: root.tx.available === true && txPane.stamped && txPane.timedReady
                            variant: "ghost"
                            implicitHeight: 34
                            iconName: "content_copy"
                            text: I18n.tr("Copy with timestamps")
                            onClicked: VideoDossier.copyTimed("timed")
                        }
                        C.PrimaryButton {
                            visible: root.tx.available === true && !txPane.stamped
                            variant: "ghost"
                            implicitHeight: 34
                            iconName: "content_copy"
                            text: I18n.tr("Copy full transcript")
                            onClicked: VideoDossier.copyTranscript()
                        }
                    }
                    Rectangle {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        radius: Tokens.radiusLg
                        color: U.withAlpha(Theme.on_surface, Theme.isDark ? 0.03 : 0.02)
                        border.width: 1
                        border.color: U.withAlpha(Theme.on_surface, 0.06)

                        // ── plain: the opening, one block of text ──
                        C.SmoothScrollView {
                            id: txScroll
                            visible: !txPane.stamped
                            anchors.fill: parent
                            anchors.margins: Tokens.space5
                            ColumnLayout {
                                width: txScroll.availableWidth
                                spacing: Tokens.space3
                                C.AppText {
                                    Layout.fillWidth: true
                                    visible: root.tx.available === true
                                    text: root.tx.preview || ""
                                    selectable: true
                                    wrapMode: Text.Wrap
                                    font.pixelSize: Tokens.fontBody + 1
                                    lineHeight: 1.5
                                    color: Theme.on_surface
                                }
                                Repeater {
                                    model: VideoDossier.transcriptLoading ? 7 : 0
                                    Skeleton { Layout.fillWidth: true; Layout.rightMargin: (index % 3) * 70 }
                                }
                                // the card shows the opening; copy + Excel hold ALL of it
                                Rectangle {
                                    visible: root.tx.truncatedPreview === true
                                    Layout.fillWidth: true
                                    implicitHeight: 54
                                    radius: Tokens.radiusMd
                                    color: U.withAlpha(root._accent, Theme.isDark ? 0.12 : 0.07)
                                    border.width: 1
                                    border.color: U.withAlpha(root._hot, 0.3)
                                    RowLayout {
                                        anchors.fill: parent
                                        anchors.leftMargin: Tokens.space4
                                        anchors.rightMargin: Tokens.space3
                                        spacing: Tokens.space3
                                        C.IconGlyph { name: "info"; size: 16; glyphColor: root._hot }
                                        C.AppText {
                                            Layout.fillWidth: true
                                            text: I18n.tr("Showing the opening. The full {n} words are in Copy and in the Excel file, nothing is cut.")
                                                  .replace("{n}", root.full(root.tx.words))
                                            wrapMode: Text.WordWrap
                                            font.pixelSize: Tokens.fontCaption
                                            color: Theme.on_surface
                                        }
                                    }
                                }
                            }
                        }

                        // ── with timestamps: one row per caption, click a time to open the video there ──
                        Item {
                            id: stampView
                            visible: txPane.stamped
                            anchors.fill: parent

                            // reading the timing
                            Column {
                                visible: txPane.timedBusy || VideoDossier.timedState === ""
                                anchors.fill: parent
                                anchors.margins: Tokens.space5
                                spacing: Tokens.space3
                                Repeater {
                                    model: 9
                                    Row {
                                        width: parent.width
                                        spacing: Tokens.space3
                                        Skeleton { width: 54; height: 22; radius: 11 }
                                        Skeleton { width: Math.max(120, (parent.width - 80) * (0.55 + (index % 4) * 0.12)) }
                                    }
                                }
                            }

                            // no timing available
                            Column {
                                visible: VideoDossier.timedState === "error"
                                anchors.centerIn: parent
                                width: Math.min(parent.width - 48, 460)
                                spacing: Tokens.space3
                                C.IconGlyph { anchors.horizontalCenter: parent.horizontalCenter; name: "info"; size: 22; glyphColor: Theme.outline }
                                C.AppText {
                                    width: parent.width
                                    horizontalAlignment: Text.AlignHCenter
                                    wrapMode: Text.WordWrap
                                    text: VideoDossier.timedError
                                    color: Theme.on_surface_var
                                    font.pixelSize: Tokens.fontBody
                                }
                                C.PrimaryButton {
                                    anchors.horizontalCenter: parent.horizontalCenter
                                    variant: "ghost"
                                    implicitHeight: 34
                                    iconName: "refresh"
                                    text: I18n.tr("Try again")
                                    onClicked: VideoDossier.loadTimed()
                                }
                            }

                            ListView {
                                id: cueList
                                visible: txPane.timedReady
                                anchors.fill: parent
                                anchors.topMargin: Tokens.space3
                                anchors.leftMargin: Tokens.space3
                                anchors.rightMargin: Tokens.space2
                                anchors.bottomMargin: 38
                                clip: true
                                spacing: 2
                                boundsBehavior: Flickable.StopAtBounds
                                cacheBuffer: 600
                                model: txPane.timedReady ? VideoDossier.timedCues : []
                                ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }
                                C.SmoothWheel { flick: cueList }

                                delegate: Rectangle {
                                    id: cueRow
                                    required property var modelData
                                    width: cueList.width - 12
                                    implicitHeight: Math.max(cueText.implicitHeight, 24) + 14
                                    radius: Tokens.radiusMd
                                    color: cueHover.hovered ? U.withAlpha(Theme.on_surface, Theme.isDark ? 0.055 : 0.04) : "transparent"
                                    Behavior on color { ColorAnimation { duration: 120 } }
                                    HoverHandler { id: cueHover }

                                    RowLayout {
                                        anchors.fill: parent
                                        anchors.leftMargin: Tokens.space2
                                        anchors.rightMargin: Tokens.space3
                                        spacing: Tokens.space3

                                        Rectangle {
                                            id: tsPill
                                            Layout.alignment: Qt.AlignTop
                                            Layout.topMargin: 1
                                            implicitWidth: Math.max(56, tsText.implicitWidth + 18)
                                            implicitHeight: 24
                                            radius: 12
                                            color: tsHover.hovered ? U.withAlpha(root._accent, Theme.isDark ? 0.3 : 0.2)
                                                                   : U.withAlpha(root._accent, Theme.isDark ? 0.15 : 0.1)
                                            border.width: 1
                                            border.color: U.withAlpha(root._hot, tsHover.hovered ? 0.6 : 0.28)
                                            Behavior on color { ColorAnimation { duration: 120 } }
                                            C.AppText {
                                                id: tsText
                                                anchors.centerIn: parent
                                                text: cueRow.modelData.ts
                                                mono: true
                                                font.pixelSize: Tokens.fontCaption
                                                weight: Tokens.weightDemiBold
                                                color: root._hot
                                            }
                                            HoverHandler { id: tsHover; cursorShape: Qt.PointingHandCursor }
                                            TapHandler { onTapped: VideoDossier.openAt(cueRow.modelData.s) }
                                        }
                                        C.AppText {
                                            id: cueText
                                            Layout.fillWidth: true
                                            text: cueRow.modelData.t
                                            selectable: true
                                            wrapMode: Text.Wrap
                                            font.pixelSize: Tokens.fontBody + 1
                                            lineHeight: 1.4
                                            color: Theme.on_surface
                                        }
                                    }
                                }
                            }

                            // footer: how many lines, which language, where the file went
                            Row {
                                visible: txPane.timedReady
                                anchors.left: parent.left
                                anchors.right: parent.right
                                anchors.bottom: parent.bottom
                                anchors.margins: Tokens.space3
                                spacing: Tokens.space3
                                C.IconGlyph { anchors.verticalCenter: parent.verticalCenter; name: "info"; size: 14; glyphColor: Theme.outline }
                                C.AppText {
                                    anchors.verticalCenter: parent.verticalCenter
                                    width: parent.width - 30
                                    elide: Text.ElideRight
                                    font.pixelSize: Tokens.fontCaption
                                    color: Theme.outline
                                    text: VideoDossier.timedCues.length + " " + I18n.tr("lines")
                                          + (VideoDossier.timedLang !== "" ? "  ·  " + VideoDossier.timedLang.toUpperCase() : "")
                                          + "  ·  " + I18n.tr("Click a time to open the video at that moment")
                                }
                            }
                        }
                    }
                }

                // 2 · COMMENTS: top + latest
                ColumnLayout {
                    spacing: Tokens.space3
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: Tokens.space2
                        Repeater {
                            model: [
                                { t: I18n.tr("Most liked"), n: VideoDossier.topComments.length, i: "favorite" },
                                { t: I18n.tr("Latest"), n: VideoDossier.latestComments.length, i: "schedule" }
                            ]
                            Rectangle {
                                required property var modelData
                                required property int index
                                readonly property bool on: root.commentTab === index
                                implicitWidth: ctRow.implicitWidth + 26
                                implicitHeight: 34
                                radius: 17
                                color: on ? U.withAlpha(root._accent, Theme.isDark ? 0.24 : 0.14)
                                          : U.withAlpha(Theme.on_surface, Theme.isDark ? 0.05 : 0.04)
                                border.width: 1
                                border.color: on ? U.withAlpha(root._hot, 0.5) : U.withAlpha(Theme.on_surface, 0.08)
                                Behavior on color { ColorAnimation { duration: 180 } }
                                Row {
                                    id: ctRow
                                    anchors.centerIn: parent
                                    spacing: 6
                                    C.IconGlyph { anchors.verticalCenter: parent.verticalCenter; name: modelData.i; size: 14; glyphColor: parent.parent.on ? root._hot : Theme.on_surface_var }
                                    C.AppText { anchors.verticalCenter: parent.verticalCenter; text: modelData.t; weight: Tokens.weightDemiBold; font.pixelSize: Tokens.fontBody; color: parent.parent.on ? Theme.text_strong : Theme.on_surface_var }
                                    C.AppText { anchors.verticalCenter: parent.verticalCenter; text: String(modelData.n); mono: true; font.pixelSize: Tokens.fontCaption; color: root._hot }
                                }
                                MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: root.commentTab = index }
                            }
                        }
                        Item { Layout.fillWidth: true }
                        C.AppText {
                            text: [root.notes.commentsOff || "", root.notes.comments || "", root.notes.commentsSource || ""]
                                  .filter(function (s) { return s !== "" }).join("  ·  ")
                            font.pixelSize: Tokens.fontCaption
                            color: root.notes.comments ? Theme.error : Theme.outline
                            elide: Text.ElideRight
                            Layout.maximumWidth: 520
                        }
                    }

                    C.SmoothScrollView {
                        id: cmScroll
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        ColumnLayout {
                            width: cmScroll.availableWidth
                            spacing: Tokens.space2

                            Repeater {
                                model: VideoDossier.commentsLoading ? 5 : 0
                                Rectangle {
                                    Layout.fillWidth: true
                                    implicitHeight: 86
                                    radius: Tokens.radiusLg
                                    color: U.withAlpha(Theme.on_surface, 0.025)
                                    Skeleton { x: 62; y: 20; width: 160 }
                                    Skeleton { x: 62; y: 46; width: parent.width - 120 }
                                    Skeleton { x: 18; y: 18; width: 32; height: 32; radius: 16 }
                                }
                            }

                            Repeater {
                                model: root.commentTab === 0 ? VideoDossier.topComments : VideoDossier.latestComments
                                Rectangle {
                                    id: cmt
                                    required property var modelData
                                    required property int index
                                    Layout.fillWidth: true
                                    implicitHeight: cmtCol.implicitHeight + Tokens.space4 * 2
                                    radius: Tokens.radiusLg
                                    color: cmtHover.hovered ? U.withAlpha(Theme.on_surface, Theme.isDark ? 0.055 : 0.04)
                                                            : U.withAlpha(Theme.on_surface, Theme.isDark ? 0.03 : 0.02)
                                    border.width: 1
                                    border.color: U.withAlpha(Theme.on_surface, cmtHover.hovered ? 0.1 : 0.05)
                                    Behavior on color { ColorAnimation { duration: 140 } }
                                    HoverHandler { id: cmtHover }

                                    // rank, on the top list
                                    C.AppText {
                                        visible: root.commentTab === 0
                                        anchors.right: parent.right
                                        anchors.top: parent.top
                                        anchors.margins: Tokens.space4
                                        text: "#" + (cmt.index + 1)
                                        mono: true
                                        weight: Tokens.weightBold
                                        font.pixelSize: Tokens.fontCaption
                                        color: cmt.index < 3 ? root._hot : Theme.outline
                                    }

                                    RowLayout {
                                        anchors.fill: parent
                                        anchors.margins: Tokens.space4
                                        spacing: Tokens.space3
                                        Rectangle {
                                            Layout.alignment: Qt.AlignTop
                                            Layout.preferredWidth: 34; Layout.preferredHeight: 34
                                            radius: 17
                                            color: U.withAlpha(root._accent, 0.22)
                                            C.AppText {
                                                anchors.centerIn: parent
                                                visible: avImg.status !== Image.Ready
                                                text: cmt.modelData.initial || "?"
                                                weight: Tokens.weightBold
                                                color: root._hot
                                            }
                                            Image {
                                                id: avImg
                                                anchors.fill: parent
                                                source: cmt.modelData.thumbId ? "image://thumbs/" + cmt.modelData.thumbId : ""
                                                sourceSize.width: 68; sourceSize.height: 68
                                                asynchronous: true
                                            }
                                        }
                                        ColumnLayout {
                                            id: cmtCol
                                            Layout.fillWidth: true
                                            spacing: 5
                                            RowLayout {
                                                spacing: Tokens.space2
                                                Layout.rightMargin: 36
                                                C.AppText {
                                                    text: cmt.modelData.author || ""
                                                    weight: Tokens.weightBold
                                                    font.pixelSize: Tokens.fontBody
                                                    color: Theme.text_strong
                                                    elide: Text.ElideRight
                                                    Layout.maximumWidth: 320
                                                }
                                                Chip { visible: cmt.modelData.pinned === true; icon: "bookmark"; label: I18n.tr("Pinned"); hot: true; implicitHeight: 22 }
                                                C.AppText {
                                                    text: (cmt.modelData.ageText || "") + (cmt.modelData.edited ? "  ·  " + I18n.tr("edited") : "")
                                                    font.pixelSize: Tokens.fontCaption
                                                    color: Theme.outline
                                                }
                                            }
                                            C.AppText {
                                                Layout.fillWidth: true
                                                text: cmt.modelData.text || ""
                                                selectable: true
                                                wrapMode: Text.Wrap
                                                font.pixelSize: Tokens.fontBody
                                                lineHeight: 1.3
                                                color: Theme.on_surface
                                            }
                                            Row {
                                                spacing: Tokens.space4
                                                Row {
                                                    spacing: 5
                                                    C.IconGlyph { anchors.verticalCenter: parent.verticalCenter; name: "favorite"; size: 13; glyphColor: (cmt.modelData.likes || 0) > 0 ? root._hot : Theme.outline }
                                                    C.AppText { anchors.verticalCenter: parent.verticalCenter; text: cmt.modelData.likesText || "0"; mono: true; font.pixelSize: Tokens.fontCaption; color: Theme.on_surface_var }
                                                }
                                                Row {
                                                    visible: (cmt.modelData.replies || 0) > 0
                                                    spacing: 5
                                                    C.IconGlyph { anchors.verticalCenter: parent.verticalCenter; name: "message"; size: 13; glyphColor: Theme.outline }
                                                    C.AppText { anchors.verticalCenter: parent.verticalCenter; text: root.num(cmt.modelData.replies) + " " + I18n.tr("replies"); font.pixelSize: Tokens.fontCaption; color: Theme.on_surface_var }
                                                }
                                                C.AppText {
                                                    visible: cmtHover.hovered
                                                    text: I18n.tr("Copy")
                                                    font.pixelSize: Tokens.fontCaption
                                                    weight: Tokens.weightDemiBold
                                                    color: root._hot
                                                    MouseArea {
                                                        anchors.fill: parent
                                                        anchors.margins: -4
                                                        cursorShape: Qt.PointingHandCursor
                                                        onClicked: VideoDossier.copyText(cmt.modelData.text || "")
                                                    }
                                                }
                                            }
                                        }
                                    }
                                }
                            }

                            C.AppText {
                                visible: !VideoDossier.commentsLoading
                                         && (root.commentTab === 0 ? VideoDossier.topComments.length : VideoDossier.latestComments.length) === 0
                                Layout.alignment: Qt.AlignHCenter
                                Layout.topMargin: Tokens.space6
                                text: root.notes.commentsOff ? root.notes.commentsOff : I18n.tr("No comments yet.")
                                color: Theme.outline
                                font.pixelSize: Tokens.fontBody
                            }
                            Item { implicitHeight: Tokens.space4 }
                        }
                    }
                }

                // 3 · DETAILS: every raw field, click a value to copy it
                C.SmoothScrollView {
                    id: dtScroll
                    GridLayout {
                        width: dtScroll.availableWidth
                        columns: root.wide ? 2 : 1
                        columnSpacing: Tokens.space4
                        rowSpacing: Tokens.space2
                        Repeater {
                            model: !root.hasMeta ? [] : [
                                [I18n.tr("Video ID"), m.videoId],
                                [I18n.tr("Video URL"), m.url],
                                [I18n.tr("Channel"), m.channelTitle],
                                [I18n.tr("Channel handle"), m.channelHandle],
                                [I18n.tr("Channel ID"), m.channelId],
                                [I18n.tr("Channel URL"), m.channelUrl],
                                [I18n.tr("Subscribers"), m.subsHidden ? I18n.tr("hidden") : root.full(m.subs)],
                                [I18n.tr("Channel views"), m.channelViews > 0 ? root.full(m.channelViews) : "—"],
                                [I18n.tr("Channel videos"), m.channelVideos > 0 ? root.full(m.channelVideos) : "—"],
                                [I18n.tr("Published"), m.publishedAt],
                                [I18n.tr("Duration"), m.durationText + "  (" + m.durationS + " s)"],
                                [I18n.tr("Views"), root.full(m.views)],
                                [I18n.tr("Likes"), m.likesHidden ? I18n.tr("hidden") : root.full(m.likes)],
                                [I18n.tr("Comments"), m.commentsHidden ? I18n.tr("hidden") : root.full(m.comments)],
                                [I18n.tr("Engagement"), root.pct(m.engagement)],
                                [I18n.tr("Outlier (views / subs)"), root.mult(m.outlier) + (m.tier ? "  ·  " + m.tier : "")],
                                [I18n.tr("Vs channel average"), root.mult(m.vsChannelAvg)],
                                [I18n.tr("Views per day"), root.full(m.viewsPerDay)],
                                [I18n.tr("Category"), m.category || "—"],
                                [I18n.tr("Language"), m.language || "—"],
                                [I18n.tr("Definition"), m.definition || "—"],
                                [I18n.tr("Captions on YouTube"), m.hasCaptions ? I18n.tr("yes") : I18n.tr("no")],
                                [I18n.tr("Licensed content"), m.licensed ? I18n.tr("yes") : I18n.tr("no")],
                                [I18n.tr("Made for kids"), m.madeForKids ? I18n.tr("yes") : I18n.tr("no")],
                                [I18n.tr("Privacy"), m.privacy || "—"],
                                [I18n.tr("License"), m.license || "—"],
                                [I18n.tr("Topics"), (m.topics || []).join(", ") || "—"],
                                [I18n.tr("Source"), m.source === "api" ? "YouTube Data API" : "yt-dlp"]
                            ]
                            Rectangle {
                                required property var modelData
                                Layout.fillWidth: true
                                implicitHeight: 44
                                radius: Tokens.radiusMd
                                color: kvHover.hovered ? U.withAlpha(Theme.on_surface, Theme.isDark ? 0.05 : 0.04)
                                                       : U.withAlpha(Theme.on_surface, Theme.isDark ? 0.025 : 0.018)
                                HoverHandler { id: kvHover; cursorShape: Qt.PointingHandCursor }
                                TapHandler { onTapped: VideoDossier.copyText(String(parent.modelData[1] || "")) }
                                RowLayout {
                                    anchors.fill: parent
                                    anchors.leftMargin: Tokens.space4
                                    anchors.rightMargin: Tokens.space4
                                    spacing: Tokens.space3
                                    C.AppText {
                                        Layout.preferredWidth: 170
                                        text: modelData[0]
                                        font.pixelSize: Tokens.fontCaption
                                        weight: Tokens.weightDemiBold
                                        color: Theme.outline
                                        elide: Text.ElideRight
                                    }
                                    C.AppText {
                                        Layout.fillWidth: true
                                        text: String(modelData[1] === undefined || modelData[1] === "" ? "—" : modelData[1])
                                        mono: true
                                        font.pixelSize: Tokens.fontCaption + 1
                                        color: Theme.text_strong
                                        elide: Text.ElideMiddle
                                    }
                                    C.IconGlyph {
                                        visible: kvHover.hovered
                                        name: "content_copy"
                                        size: 14
                                        glyphColor: root._hot
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }

        C.Toast { id: toast; anchors.horizontalCenter: parent.horizontalCenter; y: parent.height - 76; z: 10 }
    }
}
