import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import QtQuick.Shapes
import QtQuick.Effects
import NicheScope
import "." as C
import "../Utils.js" as U

// v1.9 — In-app update, "Launch Bay 2".
//
// Same API and the same five moments, driven by `Updater` (UpdateVM):
//   available    hero + a real changelog + Download & install
//   downloading  the ring around the logo IS the progress; rail + live stats
//   ready        the ring closes, a verified badge lands, 5 s countdown
//   installing   the app hands over and closes; Setup reopens it
//   error        honest message, Try again / GitHub
//
// What changed over v1.1 (it read as a dialog, not a product moment):
//   * the launch screen's language: an iridescent aura on the card edge,
//     the logo on a glass tile inside a lit orbit (the same orbit becomes
//     the download ring), accent + its two hue neighbours instead of a flat
//     blue blob;
//   * a state pill with a live dot instead of a tracked caps label;
//   * "YouTube Hunter 1.3.0": the version carries the accent, the old → new
//     capsule draws its own chevron (the icon arrow could render blank);
//   * What's new is PARSED: each "- **Title** - text" bullet becomes a row
//     with a lit marker, a bold title and a quiet body, "### Heading"
//     lines become section labels, a count sits in the header and the list
//     fades out at the bottom while it scrolls. Unknown shapes fall back to
//     the old markdown text;
//   * a reassurance line under the buttons (your data stays), the size on
//     the main button, GitHub demoted to a quiet link.
Dialog {
    id: root
    modal: true
    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(600, (parent ? parent.width : 900) - 64)
    padding: 0
    closePolicy: Updater.state === "installing" ? Popup.NoAutoClose
                                                : (Popup.CloseOnEscape | Popup.CloseOnPressOutside)

    readonly property string st: Updater.state
    readonly property bool _motion: root.visible && Tokens.appActive
    property int countdown: 5

    // palette: the accent and its two neighbours round the wheel
    readonly property color _accent: Theme.primary
    readonly property color _accentHot: Theme.isDark ? U.mix(_accent, "#FFFFFF", 0.4) : U.mix(_accent, "#FFFFFF", 0.12)
    function _shift(c, d) {
        if (c.hslHue < 0) return c
        return Qt.hsla((c.hslHue + d + 1.0) % 1.0, Math.min(1, c.hslSaturation * 0.9), c.hslLightness, 1)
    }
    readonly property color _auroraA: _shift(_accent, 0.11)
    readonly property color _auroraB: _shift(_accent, -0.08)
    readonly property color _stateColor: st === "error" ? Theme.error
                                       : st === "ready" ? Theme.secondary_container
                                       : _accent

    readonly property real _cardR: 28

    function present() {
        countdown = 5
        showAll = false
        open()
    }

    // SVG path for a rounded rectangle (circular corners)
    function _roundRect(x, y, w, h, r) {
        return "M" + (x + r) + " " + y
             + " H" + (x + w - r) + " A" + r + " " + r + " 0 0 1 " + (x + w) + " " + (y + r)
             + " V" + (y + h - r) + " A" + r + " " + r + " 0 0 1 " + (x + w - r) + " " + (y + h)
             + " H" + (x + r) + " A" + r + " " + r + " 0 0 1 " + x + " " + (y + h - r)
             + " V" + (y + r) + " A" + r + " " + r + " 0 0 1 " + (x + r) + " " + y + " Z "
    }

    // ── release notes, parsed ──
    // [{kind: "head"|"item"|"para", title, body}]
    function _strip(s) {
        return String(s || "").replace(/\*\*|__/g, "").replace(/`/g, "").trim()
    }
    function _item(s) {
        var m = s.match(/^\*\*(.+?)\*\*\s*(?:\([^)]*\)\s*)?[-–—:·]?\s*(.*)$/)
        if (m) return { kind: "item", title: root._strip(m[1]), body: m[2] }
        return { kind: "item", title: "", body: s }
    }
    function parseNotes(md) {
        var out = []
        var cur = null
        var lines = String(md || "").split(/\r?\n/)
        for (var i = 0; i < lines.length; i++) {
            var raw = lines[i]
            var t = raw.trim()
            if (t === "" || /^[-*_]{3,}$/.test(t)) { cur = null; continue }
            var h = t.match(/^#{1,6}\s+(.*)$/)
            if (h) { out.push({ kind: "head", title: root._strip(h[1]), body: "" }); cur = null; continue }
            var b = t.match(/^(?:[-*•+]|\d+[.)])\s+(.*)$/)
            if (b) { cur = root._item(b[1]); out.push(cur); continue }
            if (cur) { cur.body = (cur.body + " " + t).trim(); continue }
            out.push({ kind: "para", title: "", body: t })
        }
        return out
    }
    readonly property var notesModel: parseNotes(Updater.notes)
    readonly property int notesCount: {
        var n = 0
        for (var i = 0; i < notesModel.length; i++) if (notesModel[i].kind === "item") n++
        return n
    }
    readonly property bool notesParsed: notesCount > 0
    property bool showAll: false

    // auto-restart once the installer is downloaded and verified
    Timer {
        id: countTimer
        interval: 1000
        repeat: true
        running: root.opened && root.st === "ready" && Updater.packaged
        onTriggered: {
            if (root.countdown > 1) root.countdown--
            else { stop(); Updater.installNow() }
        }
    }
    onStChanged: if (st === "ready") countdown = 5

    Overlay.modal: C.DialogScrim { }
    enter: Transition {
        ParallelAnimation {
            NumberAnimation { property: "opacity"; from: 0; to: 1; duration: 260; easing.type: Easing.OutCubic }
            NumberAnimation { property: "scale"; from: 0.95; to: 1; duration: 460; easing.type: Easing.OutBack; easing.overshoot: 1.1 }
        }
    }
    exit: Transition {
        ParallelAnimation {
            NumberAnimation { property: "opacity"; to: 0; duration: 160; easing.type: Easing.OutCubic }
            NumberAnimation { property: "scale"; to: 0.98; duration: 160; easing.type: Easing.OutCubic }
        }
    }

    // ── glass card: aurora, grain-free glow, iridescent edge ──
    background: Item {
        id: card

        Rectangle {
            anchors.fill: parent
            radius: root._cardR
            color: Theme.isDark ? U.mix(Theme.surface, "#000000", 0.18) : Theme.surface
            border.width: 1
            border.color: U.withAlpha(Theme.on_surface, Theme.isDark ? 0.08 : 0.1)
        }

        // the aurora: three soft lights behind the hero, clipped to the card
        Item {
            id: auroraClip
            anchors.fill: parent
            anchors.margins: 1
            clip: true
            Item {
                id: aurora
                width: parent.width
                height: 320
                layer.enabled: true
                layer.effect: MultiEffect { blurEnabled: true; blur: 1.0; blurMax: 64 }
                opacity: Theme.isDark ? 0.9 : 0.55
                Rectangle {
                    width: 260; height: 260; radius: 130
                    color: U.withAlpha(root._accent, Theme.isDark ? 0.42 : 0.26)
                    y: -170
                    x: aurora.width / 2 - 130
                    SequentialAnimation on x {
                        running: root._motion; loops: Animation.Infinite
                        NumberAnimation { to: aurora.width / 2 - 60; duration: 8000; easing.type: Easing.InOutSine }
                        NumberAnimation { to: aurora.width / 2 - 200; duration: 8000; easing.type: Easing.InOutSine }
                    }
                }
                Rectangle {
                    width: 200; height: 200; radius: 100
                    color: U.withAlpha(root._auroraA, Theme.isDark ? 0.3 : 0.18)
                    y: -120
                    x: aurora.width - 150
                    SequentialAnimation on y {
                        running: root._motion; loops: Animation.Infinite
                        NumberAnimation { to: -80; duration: 6200; easing.type: Easing.InOutSine }
                        NumberAnimation { to: -140; duration: 6200; easing.type: Easing.InOutSine }
                    }
                }
                Rectangle {
                    width: 200; height: 200; radius: 100
                    color: U.withAlpha(root._auroraB, Theme.isDark ? 0.26 : 0.16)
                    y: -110
                    x: -60
                    SequentialAnimation on y {
                        running: root._motion; loops: Animation.Infinite
                        NumberAnimation { to: -150; duration: 7400; easing.type: Easing.InOutSine }
                        NumberAnimation { to: -100; duration: 7400; easing.type: Easing.InOutSine }
                    }
                }
            }
            // the lights fade into the card body, no hard blob edge
            Rectangle {
                anchors.left: parent.left
                anchors.right: parent.right
                y: 120
                height: 200
                gradient: Gradient {
                    GradientStop { position: 0.0; color: "transparent" }
                    GradientStop { position: 1.0; color: Theme.isDark ? U.mix(Theme.surface, "#000000", 0.18) : Theme.surface }
                }
            }
        }

        // iridescent edge: the accent and its neighbours on a conic fill,
        // a 1.25 px annulus that slowly turns (the splash's aura, in small)
        Shape {
            id: edge
            anchors.fill: parent
            opacity: Theme.isDark ? 0.75 : 0.55
            property real spin: 0
            NumberAnimation on spin {
                running: root._motion
                from: 0; to: 360; duration: 14000; loops: Animation.Infinite
            }
            ShapePath {
                strokeWidth: -1
                strokeColor: "transparent"
                fillRule: ShapePath.OddEvenFill
                fillGradient: ConicalGradient {
                    centerX: edge.width / 2; centerY: edge.height / 2
                    angle: 90 + edge.spin
                    GradientStop { position: 0.0;  color: U.withAlpha(root._auroraA, 0.9) }
                    GradientStop { position: 0.18; color: U.withAlpha(root._accent, 0.05) }
                    GradientStop { position: 0.34; color: root._accentHot }
                    GradientStop { position: 0.5;  color: U.withAlpha(root._auroraB, 0.7) }
                    GradientStop { position: 0.68; color: U.withAlpha(root._accent, 0.05) }
                    GradientStop { position: 0.84; color: U.withAlpha(root._auroraA, 0.5) }
                    GradientStop { position: 1.0;  color: U.withAlpha(root._auroraA, 0.9) }
                }
                PathSvg {
                    path: edge.width > 8
                          ? root._roundRect(0, 0, edge.width, edge.height, root._cardR)
                            + root._roundRect(1.25, 1.25, edge.width - 2.5, edge.height - 2.5, root._cardR - 1.25)
                          : ""
                }
            }
        }

        // specular top edge: the card reads as glass
        Rectangle {
            anchors.top: parent.top
            anchors.topMargin: 1
            anchors.horizontalCenter: parent.horizontalCenter
            width: parent.width - root._cardR * 2
            height: 1
            gradient: Gradient {
                orientation: Gradient.Horizontal
                GradientStop { position: 0.0; color: "transparent" }
                GradientStop { position: 0.5; color: Qt.rgba(1, 1, 1, Theme.isDark ? 0.3 : 0.7) }
                GradientStop { position: 1.0; color: "transparent" }
            }
        }
    }

    contentItem: ColumnLayout {
        spacing: 0

        // close
        C.IconButton {
            Layout.alignment: Qt.AlignRight
            Layout.topMargin: Tokens.space3
            Layout.rightMargin: Tokens.space3
            iconName: "close"
            visible: root.st !== "installing"
            onClicked: root.close()
        }

        // ══ hero ══
        ColumnLayout {
            Layout.fillWidth: true
            Layout.leftMargin: Tokens.space6
            Layout.rightMargin: Tokens.space6
            spacing: 0

            // the logo on a glass tile inside a lit orbit. The orbit is the
            // download ring; it closes and a verified badge lands when ready.
            Item {
                id: lens
                Layout.alignment: Qt.AlignHCenter
                implicitWidth: 136
                implicitHeight: 136
                readonly property real c: 68
                readonly property real ringR: 60
                readonly property real prog: root.st === "downloading" ? Math.max(0, Math.min(1, Updater.progress))
                                            : (root.st === "ready" || root.st === "installing") ? 1 : 0
                property real progShown: 0
                Behavior on progShown { NumberAnimation { duration: 420; easing.type: Easing.OutCubic } }
                Component.onCompleted: progShown = Qt.binding(function () { return lens.prog })

                // soft halo behind the tile
                Rectangle {
                    anchors.centerIn: parent
                    width: 112; height: 112; radius: 56
                    color: U.withAlpha(root._stateColor, Theme.isDark ? 0.22 : 0.12)
                    layer.enabled: true
                    layer.effect: MultiEffect { blurEnabled: true; blur: 1.0; blurMax: 40 }
                    SequentialAnimation on opacity {
                        running: root._motion; loops: Animation.Infinite
                        NumberAnimation { from: 1; to: 0.55; duration: 2400; easing.type: Easing.InOutSine }
                        NumberAnimation { from: 0.55; to: 1; duration: 2400; easing.type: Easing.InOutSine }
                    }
                }

                // orbit track
                Shape {
                    anchors.fill: parent
                    preferredRendererType: Shape.CurveRenderer
                    ShapePath {
                        strokeWidth: 1
                        strokeColor: U.withAlpha(Theme.on_surface, Theme.isDark ? 0.1 : 0.12)
                        fillColor: "transparent"
                        PathAngleArc { centerX: lens.c; centerY: lens.c; radiusX: lens.ringR; radiusY: lens.ringR; startAngle: 0; sweepAngle: 360 }
                    }
                }
                // comet: circles the orbit while there is nothing to measure
                Shape {
                    id: comet
                    anchors.fill: parent
                    preferredRendererType: Shape.CurveRenderer
                    visible: root.st === "available" || root.st === "error" || root.st === "installing"
                    opacity: root.st === "error" ? 0.5 : 1
                    RotationAnimation on rotation {
                        running: root._motion && comet.visible
                        from: 0; to: 360
                        duration: root.st === "installing" ? 1400 : 6000
                        loops: Animation.Infinite
                    }
                    ShapePath {
                        strokeWidth: -1
                        strokeColor: "transparent"
                        fillRule: ShapePath.OddEvenFill
                        fillGradient: ConicalGradient {
                            centerX: lens.c; centerY: lens.c; angle: 90
                            GradientStop { position: 0.0;   color: U.withAlpha(root._accentHot, 0.0) }
                            GradientStop { position: 0.006; color: root.st === "error" ? Theme.error : root._accentHot }
                            GradientStop { position: 0.12;  color: U.withAlpha(root.st === "error" ? Theme.error : root._accent, 0.5) }
                            GradientStop { position: 0.4;   color: U.withAlpha(root._accent, 0.0) }
                            GradientStop { position: 1.0;   color: U.withAlpha(root._accent, 0.0) }
                        }
                        PathAngleArc { centerX: lens.c; centerY: lens.c; radiusX: lens.ringR + 1; radiusY: lens.ringR + 1; startAngle: 0; sweepAngle: 360 }
                        PathAngleArc { centerX: lens.c; centerY: lens.c; radiusX: lens.ringR - 1; radiusY: lens.ringR - 1; startAngle: 0; sweepAngle: 360 }
                    }
                }
                // progress: soft underglow + the arc + a glowing head
                Repeater {
                    model: [{ w: 8, a: Theme.isDark ? 0.14 : 0.1 }, { w: 2.5, a: 1.0 }]
                    Shape {
                        id: arc
                        required property var modelData
                        anchors.fill: parent
                        preferredRendererType: Shape.CurveRenderer
                        visible: lens.progShown > 0.003
                        opacity: arc.modelData.a
                        ShapePath {
                            strokeWidth: arc.modelData.w
                            strokeColor: root.st === "ready" ? Theme.secondary_container
                                                             : U.mix(root._accent, root._accentHot, 0.35)
                            fillColor: "transparent"
                            capStyle: ShapePath.RoundCap
                            PathAngleArc {
                                centerX: lens.c; centerY: lens.c
                                radiusX: lens.ringR; radiusY: lens.ringR
                                startAngle: -90
                                sweepAngle: 360 * lens.progShown
                            }
                        }
                    }
                }
                Rectangle {
                    readonly property real _a: (-90 + 360 * lens.progShown) * Math.PI / 180
                    visible: root.st === "downloading" && lens.progShown > 0.01
                    width: 7; height: 7; radius: 3.5
                    antialiasing: true
                    color: Theme.isDark ? U.mix(root._accentHot, "#FFFFFF", 0.5) : root._accentHot
                    x: lens.c + lens.ringR * Math.cos(_a) - width / 2
                    y: lens.c + lens.ringR * Math.sin(_a) - height / 2
                }

                // the glass tile
                Rectangle {
                    id: tile
                    anchors.centerIn: parent
                    width: 88; height: 88; radius: 26
                    color: Theme.isDark ? "#0d0d10" : "#15151a"
                    border.width: 1
                    border.color: Qt.rgba(1, 1, 1, 0.1)
                    // specular top
                    Rectangle {
                        anchors.top: parent.top
                        anchors.topMargin: 1
                        anchors.horizontalCenter: parent.horizontalCenter
                        width: parent.width - 30
                        height: 1
                        gradient: Gradient {
                            orientation: Gradient.Horizontal
                            GradientStop { position: 0.0; color: "transparent" }
                            GradientStop { position: 0.5; color: Qt.rgba(1, 1, 1, 0.32) }
                            GradientStop { position: 1.0; color: "transparent" }
                        }
                    }
                    Image {
                        anchors.centerIn: parent
                        width: 62; height: 62
                        source: Updater.logoUrl
                        fillMode: Image.PreserveAspectFit
                        smooth: true; mipmap: true
                    }
                }

                // verified badge, lands when the download is checked
                Rectangle {
                    x: tile.x + tile.width - width + 8
                    y: tile.y + tile.height - height + 8
                    width: 30; height: 30; radius: 15
                    color: Theme.secondary_container
                    border.width: 3
                    border.color: Theme.isDark ? U.mix(Theme.surface, "#000000", 0.18) : Theme.surface
                    scale: root.st === "ready" ? 1 : 0
                    visible: scale > 0.01
                    Behavior on scale { NumberAnimation { duration: 460; easing.type: Easing.OutBack; easing.overshoot: 2.2 } }
                    C.IconGlyph {
                        anchors.centerIn: parent
                        name: "check"; size: 16; fill: true
                        glyphColor: Theme.isDark ? "#0b0b0d" : "#FFFFFF"
                    }
                }
            }

            // state pill: live dot + what is happening
            Rectangle {
                Layout.alignment: Qt.AlignHCenter
                Layout.topMargin: Tokens.space2
                implicitWidth: pillRow.implicitWidth + 24
                implicitHeight: 26
                radius: 13
                color: U.withAlpha(root._stateColor, Theme.isDark ? 0.12 : 0.1)
                border.width: 1
                border.color: U.withAlpha(root._stateColor, 0.3)
                Row {
                    id: pillRow
                    anchors.centerIn: parent
                    spacing: 8
                    Rectangle {
                        id: pillDot
                        anchors.verticalCenter: parent.verticalCenter
                        width: 6; height: 6; radius: 3
                        color: root._stateColor
                        SequentialAnimation on opacity {
                            running: root._motion && root.st !== "ready"
                            loops: Animation.Infinite
                            NumberAnimation { from: 1; to: 0.3; duration: 900; easing.type: Easing.InOutSine }
                            NumberAnimation { from: 0.3; to: 1; duration: 900; easing.type: Easing.InOutSine }
                        }
                    }
                    C.AppText {
                        anchors.verticalCenter: parent.verticalCenter
                        text: root.st === "ready" ? I18n.tr("Verified · ready to install")
                              : root.st === "downloading" ? I18n.tr("Downloading")
                              : root.st === "installing" ? I18n.tr("Installing")
                              : root.st === "error" ? I18n.tr("Update interrupted")
                              : I18n.tr("New version available")
                        weight: Tokens.weightBold
                        font.pixelSize: Tokens.fontMicro
                        font.letterSpacing: 0.6
                        color: root.st === "error" ? Theme.error
                               : root.st === "ready" ? Theme.secondary_container
                               : (Theme.isDark ? root._accentHot : root._accent)
                    }
                }
            }

            // title: the version carries the accent
            Row {
                Layout.alignment: Qt.AlignHCenter
                Layout.topMargin: Tokens.space3
                Layout.maximumWidth: root.width - Tokens.space6 * 2
                spacing: 9
                LayoutMirroring.enabled: false
                C.AppText {
                    text: root.st === "ready" ? I18n.tr("Ready to install")
                          : root.st === "installing" ? I18n.tr("Installing")
                          : root.st === "error" ? I18n.tr("The update didn't finish")
                          : root.st === "downloading" ? I18n.tr("Downloading")
                          : "YouTube Hunter"
                    weight: Tokens.weightExtraBold
                    font.pixelSize: 28
                    font.letterSpacing: -0.4
                    color: Theme.text_strong
                }
                C.AppText {
                    visible: root.st !== "error" && Updater.latestVersion !== ""
                    text: Updater.latestVersion
                    weight: Tokens.weightExtraBold
                    font.pixelSize: 28
                    font.letterSpacing: -0.4
                    color: Theme.isDark ? root._accentHot : root._accent
                }
            }

            // one quiet line under the title
            C.AppText {
                Layout.fillWidth: true
                Layout.topMargin: Tokens.space1
                horizontalAlignment: Text.AlignHCenter
                wrapMode: Text.Wrap
                font.pixelSize: Tokens.fontBody
                color: Theme.on_surface_var
                text: root.st === "installing"
                      ? I18n.tr("YouTube Hunter will close and reopen by itself in a moment.")
                      : root.st === "ready"
                        ? (Updater.packaged
                           ? I18n.tr("Restarting in {n}s to install. Your data and settings stay as they are.").replace("{n}", root.countdown)
                           : I18n.tr("Running from source: the installer will open as a normal setup wizard."))
                      : root.st === "downloading"
                        ? I18n.tr("You can keep working. It installs when you say so.")
                      : root.st === "error"
                        ? I18n.tr("Nothing changed on your computer. Try again, or grab it from GitHub.")
                      : (Updater.releaseName !== "" && Updater.releaseName.indexOf(Updater.latestVersion) < 0
                         ? Updater.releaseName
                         : I18n.tr("A fresh build is ready for you."))
            }

            // version capsule: old › new, and date · size
            Row {
                Layout.alignment: Qt.AlignHCenter
                Layout.topMargin: Tokens.space4
                visible: root.st === "available"
                spacing: Tokens.space2
                LayoutMirroring.enabled: false
                Rectangle {
                    height: 30; radius: 15
                    width: capRow.implicitWidth + 24
                    color: U.withAlpha(Theme.on_surface, Theme.isDark ? 0.05 : 0.04)
                    border.width: 1; border.color: Theme.border
                    Row {
                        id: capRow
                        anchors.centerIn: parent
                        spacing: 10
                        C.AppText {
                            anchors.verticalCenter: parent.verticalCenter
                            text: "v" + Updater.currentVersion
                            mono: true
                            font.pixelSize: Tokens.fontCaption
                            color: Theme.on_surface_var
                            opacity: 0.8
                        }
                        // a drawn chevron pair, travelling right
                        Item {
                            anchors.verticalCenter: parent.verticalCenter
                            width: 16; height: 10
                            clip: true
                            Shape {
                                id: chev
                                width: 16; height: 10
                                preferredRendererType: Shape.CurveRenderer
                                SequentialAnimation on x {
                                    running: root._motion; loops: Animation.Infinite
                                    NumberAnimation { from: -2; to: 2; duration: 700; easing.type: Easing.InOutSine }
                                    NumberAnimation { from: 2; to: -2; duration: 700; easing.type: Easing.InOutSine }
                                }
                                ShapePath {
                                    strokeWidth: 1.6
                                    strokeColor: U.withAlpha(root._accent, 0.45)
                                    fillColor: "transparent"
                                    capStyle: ShapePath.RoundCap
                                    joinStyle: ShapePath.RoundJoin
                                    PathPolyline { path: [Qt.point(3, 1.5), Qt.point(7, 5), Qt.point(3, 8.5)] }
                                }
                                ShapePath {
                                    strokeWidth: 1.6
                                    strokeColor: Theme.isDark ? root._accentHot : root._accent
                                    fillColor: "transparent"
                                    capStyle: ShapePath.RoundCap
                                    joinStyle: ShapePath.RoundJoin
                                    PathPolyline { path: [Qt.point(9, 1.5), Qt.point(13, 5), Qt.point(9, 8.5)] }
                                }
                            }
                        }
                        C.AppText {
                            anchors.verticalCenter: parent.verticalCenter
                            text: "v" + Updater.latestVersion
                            mono: true
                            weight: Tokens.weightExtraBold
                            font.pixelSize: Tokens.fontCaption
                            color: Theme.isDark ? root._accentHot : root._accent
                        }
                    }
                }
                Rectangle {
                    visible: Updater.published !== "" || Updater.sizeText !== ""
                    height: 30; radius: 15
                    width: metaLbl.implicitWidth + 24
                    color: U.withAlpha(Theme.on_surface, Theme.isDark ? 0.05 : 0.04)
                    border.width: 1; border.color: Theme.border
                    C.AppText {
                        id: metaLbl
                        anchors.centerIn: parent
                        text: [Updater.published, Updater.sizeText].filter(function (x) { return x !== "" }).join("  ·  ")
                        mono: true
                        font.pixelSize: Tokens.fontCaption
                        color: Theme.on_surface_var
                    }
                }
            }
        }

        // ══ what's new ══
        Rectangle {
            id: notesCard
            Layout.fillWidth: true
            Layout.topMargin: Tokens.space5
            Layout.leftMargin: Tokens.space6
            Layout.rightMargin: Tokens.space6
            visible: root.st === "available" || root.st === "error"
            readonly property real _cap: root.showAll ? 380 : 236
            implicitHeight: Math.min(_cap, notesHead.height + notesBody.implicitHeight + Tokens.space4 * 2 + Tokens.space3)
            Behavior on implicitHeight { NumberAnimation { duration: 260; easing.type: Easing.OutCubic } }
            radius: 20
            color: U.withAlpha(Theme.on_surface, Theme.isDark ? 0.035 : 0.025)
            border.width: 1
            border.color: Theme.border
            clip: true

            // header: icon, title, count, expand
            RowLayout {
                id: notesHead
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.top: parent.top
                anchors.margins: Tokens.space4
                anchors.bottomMargin: 0
                spacing: Tokens.space2
                C.IconGlyph { name: "sparkling"; size: 15; fill: true; glyphColor: Theme.isDark ? root._accentHot : root._accent }
                C.AppText {
                    text: I18n.tr("What's new")
                    weight: Tokens.weightExtraBold
                    color: Theme.text_strong
                }
                Rectangle {
                    visible: root.notesCount > 1
                    implicitWidth: cntLbl.implicitWidth + 14
                    implicitHeight: 20
                    radius: 10
                    color: U.withAlpha(root._accent, 0.12)
                    C.AppText {
                        id: cntLbl
                        anchors.centerIn: parent
                        text: root.notesCount
                        mono: true
                        weight: Tokens.weightBold
                        font.pixelSize: Tokens.fontMicro
                        color: Theme.isDark ? root._accentHot : root._accent
                    }
                }
                Item { Layout.fillWidth: true }
                C.AppText {
                    visible: notesFlick.contentHeight > notesFlick.height + 4 || root.showAll
                    text: root.showAll ? I18n.tr("Show less") : I18n.tr("Show all")
                    weight: Tokens.weightBold
                    font.pixelSize: Tokens.fontCaption
                    color: moreMouse.containsMouse ? Theme.text_strong : Theme.on_surface_var
                    MouseArea {
                        id: moreMouse
                        anchors.fill: parent
                        anchors.margins: -6
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: root.showAll = !root.showAll
                    }
                }
            }

            Flickable {
                id: notesFlick
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.top: notesHead.bottom
                anchors.bottom: parent.bottom
                anchors.leftMargin: Tokens.space4
                anchors.rightMargin: Tokens.space3
                anchors.topMargin: Tokens.space3
                contentHeight: notesBody.implicitHeight + Tokens.space4
                boundsBehavior: Flickable.StopAtBounds
                clip: true
                ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

                ColumnLayout {
                    id: notesBody
                    width: notesFlick.width - Tokens.space3
                    spacing: Tokens.space3

                    // parsed: one row per change
                    Repeater {
                        model: root.notesParsed ? root.notesModel : []
                        delegate: Item {
                            id: noteRow
                            required property var modelData
                            Layout.fillWidth: true
                            implicitHeight: noteRow.modelData.kind === "head" ? headLbl.implicitHeight + 4
                                                                              : Math.max(18, noteCol.implicitHeight)

                            C.AppText {
                                id: headLbl
                                visible: noteRow.modelData.kind === "head"
                                anchors.left: parent.left
                                anchors.right: parent.right
                                anchors.bottom: parent.bottom
                                text: String(noteRow.modelData.title).toUpperCase()
                                weight: Tokens.weightBold
                                font.pixelSize: Tokens.fontMicro
                                font.letterSpacing: 1.4
                                color: Theme.on_surface_var
                                elide: Text.ElideRight
                            }

                            // lit marker
                            Rectangle {
                                visible: noteRow.modelData.kind !== "head"
                                x: 0
                                y: 7
                                width: 6; height: 6; radius: 3
                                color: Theme.isDark ? root._accentHot : root._accent
                                Rectangle {
                                    anchors.centerIn: parent
                                    width: 14; height: 14; radius: 7
                                    color: U.withAlpha(root._accent, 0.16)
                                    z: -1
                                }
                            }
                            ColumnLayout {
                                id: noteCol
                                visible: noteRow.modelData.kind !== "head"
                                anchors.left: parent.left
                                anchors.right: parent.right
                                anchors.leftMargin: 18
                                spacing: 2
                                C.AppText {
                                    Layout.fillWidth: true
                                    visible: noteRow.modelData.title !== ""
                                    text: noteRow.modelData.title
                                    weight: Tokens.weightBold
                                    font.pixelSize: Tokens.fontBody
                                    color: Theme.text_strong
                                    wrapMode: Text.Wrap
                                }
                                C.AppText {
                                    Layout.fillWidth: true
                                    visible: noteRow.modelData.body !== ""
                                    text: noteRow.modelData.body
                                    textFormat: Text.MarkdownText
                                    wrapMode: Text.Wrap
                                    font.pixelSize: Tokens.fontCaption + 1
                                    lineHeight: 1.2
                                    color: Theme.on_surface_var
                                    onLinkActivated: (link) => Qt.openUrlExternally(link)
                                }
                            }
                        }
                    }

                    // fallback: the notes as written
                    C.AppText {
                        Layout.fillWidth: true
                        visible: !root.notesParsed
                        text: Updater.notes
                        textFormat: Text.MarkdownText
                        wrapMode: Text.Wrap
                        color: Theme.on_surface
                        font.pixelSize: Tokens.fontBody
                        lineHeight: 1.25
                        onLinkActivated: (link) => Qt.openUrlExternally(link)
                    }
                }
            }

            // the list fades out at the bottom while there is more below
            Rectangle {
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.bottom: parent.bottom
                anchors.margins: 1
                height: 40
                radius: 19
                visible: !notesFlick.atYEnd
                gradient: Gradient {
                    GradientStop { position: 0.0; color: "transparent" }
                    GradientStop { position: 1.0; color: Theme.isDark ? U.mix(Theme.surface, "#000000", 0.14) : Theme.surface }
                }
            }
        }

        // ══ download progress ══
        ColumnLayout {
            Layout.fillWidth: true
            Layout.topMargin: Tokens.space5
            Layout.leftMargin: Tokens.space6
            Layout.rightMargin: Tokens.space6
            visible: root.st === "downloading"
            spacing: Tokens.space3

            RowLayout {
                Layout.fillWidth: true
                LayoutMirroring.enabled: false
                C.AppText {
                    id: pct
                    property real shown: 0
                    Behavior on shown { NumberAnimation { duration: 300; easing.type: Easing.OutCubic } }
                    Component.onCompleted: shown = Qt.binding(function () { return Updater.progress * 100 })
                    text: Math.floor(shown) + "%"
                    mono: true
                    weight: Tokens.weightExtraBold
                    font.pixelSize: 40
                    font.features: ({ "tnum": 1 })
                    color: Theme.text_strong
                }
                Item { Layout.fillWidth: true }
                ColumnLayout {
                    spacing: 2
                    C.AppText { Layout.alignment: Qt.AlignRight; text: Updater.receivedText; mono: true; font.pixelSize: Tokens.fontCaption; color: Theme.on_surface }
                    C.AppText {
                        Layout.alignment: Qt.AlignRight
                        text: [Updater.speedText, Updater.etaText !== "" ? I18n.tr("{t} left").replace("{t}", Updater.etaText) : ""]
                              .filter(function (x) { return x !== "" }).join("  ·  ")
                        mono: true; font.pixelSize: Tokens.fontCaption; color: Theme.on_surface_var
                    }
                }
            }

            // the rail: track, lit fill, travelling shimmer, glowing head
            Item {
                Layout.fillWidth: true
                implicitHeight: 10
                LayoutMirroring.enabled: false
                Rectangle {
                    anchors.fill: parent
                    radius: 5
                    color: U.withAlpha(Theme.on_surface, 0.08)
                }
                Rectangle {
                    id: fillBar
                    height: parent.height
                    radius: 5
                    width: Math.max(height, parent.width * Updater.progress)
                    Behavior on width { NumberAnimation { duration: 280; easing.type: Easing.OutCubic } }
                    clip: true
                    gradient: Gradient {
                        orientation: Gradient.Horizontal
                        GradientStop { position: 0.0; color: U.mix(root._auroraB, "#000000", 0.2) }
                        GradientStop { position: 0.6; color: root._accent }
                        GradientStop { position: 1.0; color: root._accentHot }
                    }
                    Rectangle {
                        width: 70; height: parent.height
                        gradient: Gradient {
                            orientation: Gradient.Horizontal
                            GradientStop { position: 0.0; color: Qt.rgba(1, 1, 1, 0) }
                            GradientStop { position: 0.5; color: Qt.rgba(1, 1, 1, 0.35) }
                            GradientStop { position: 1.0; color: Qt.rgba(1, 1, 1, 0) }
                        }
                        NumberAnimation on x {
                            running: root._motion && root.st === "downloading"
                            from: -70; to: fillBar.width; duration: 1300; loops: Animation.Infinite
                        }
                    }
                }
                Rectangle {
                    width: 18; height: 18; radius: 9
                    anchors.verticalCenter: parent.verticalCenter
                    x: fillBar.width - width / 2 - 3
                    color: U.withAlpha(root._accentHot, 0.4)
                    layer.enabled: true
                    layer.effect: MultiEffect { blurEnabled: true; blur: 0.8; blurMax: 16 }
                }
            }
        }

        // ══ error ══
        Rectangle {
            Layout.fillWidth: true
            Layout.topMargin: Tokens.space3
            Layout.leftMargin: Tokens.space6
            Layout.rightMargin: Tokens.space6
            visible: root.st === "error" && Updater.error !== ""
            implicitHeight: errRow.implicitHeight + Tokens.space3 * 2
            radius: 14
            color: U.withAlpha(Theme.error, 0.09)
            border.width: 1
            border.color: U.withAlpha(Theme.error, 0.32)
            RowLayout {
                id: errRow
                anchors.fill: parent
                anchors.margins: Tokens.space3
                spacing: Tokens.space2
                C.IconGlyph { Layout.alignment: Qt.AlignTop; name: "error"; size: 16; fill: true; glyphColor: Theme.error }
                C.AppText {
                    Layout.fillWidth: true
                    text: Updater.error
                    wrapMode: Text.Wrap
                    color: Theme.on_surface
                    font.pixelSize: Tokens.fontCaption
                }
            }
        }

        // ══ actions ══
        RowLayout {
            Layout.fillWidth: true
            Layout.leftMargin: Tokens.space6
            Layout.rightMargin: Tokens.space6
            Layout.topMargin: Tokens.space5
            spacing: Tokens.space3

            // GitHub: a quiet link, not a third button.
            // v2.1.1: the hit area is a sibling of the Row, not a child (a Row
            // cannot lay out a child with anchors.fill, it logged a warning)
            Item {
                visible: root.st === "available" || root.st === "error"
                implicitWidth: ghRow.implicitWidth
                implicitHeight: ghRow.implicitHeight
            Row {
                id: ghRow
                anchors.centerIn: parent
                spacing: 6
                opacity: ghMouse.containsMouse ? 1 : 0.75
                C.IconGlyph {
                    anchors.verticalCenter: parent.verticalCenter
                    name: "open_in_new"; size: 14
                    glyphColor: Theme.on_surface_var
                }
                C.AppText {
                    anchors.verticalCenter: parent.verticalCenter
                    text: I18n.tr("Release on GitHub")
                    weight: Tokens.weightMedium
                    font.pixelSize: Tokens.fontCaption
                    font.underline: ghMouse.containsMouse
                    color: Theme.on_surface_var
                }
            }
                MouseArea {
                    id: ghMouse
                    anchors.fill: parent
                    anchors.margins: -6
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: Updater.openReleasePage()
                }
            }
            Item { Layout.fillWidth: true }
            C.PrimaryButton {
                variant: "ghost"
                visible: root.st !== "installing"
                text: root.st === "downloading" ? I18n.tr("Cancel") : I18n.tr("Later")
                implicitHeight: 46
                onClicked: {
                    if (root.st === "downloading") Updater.cancelDownload()
                    else { countTimer.stop(); root.close() }
                }
            }
            C.PrimaryButton {
                visible: root.st === "available" || root.st === "error"
                iconName: root.st === "error" ? "refresh" : "download"
                text: root.st === "error" ? I18n.tr("Try again")
                      : (Updater.sizeText !== "" ? I18n.tr("Download & install · {s}").replace("{s}", Updater.sizeText)
                                                 : I18n.tr("Download & install"))
                implicitHeight: 46
                onClicked: Updater.latestVersion === "" ? Updater.checkNow() : Updater.startDownload()
            }
            C.PrimaryButton {
                visible: root.st === "ready"
                iconName: "restart_alt"
                text: Updater.packaged ? I18n.tr("Restart now") : I18n.tr("Open installer")
                implicitHeight: 46
                onClicked: { countTimer.stop(); Updater.installNow() }
            }
        }

        // reassurance, the one line people look for before they click
        Row {
            Layout.alignment: Qt.AlignHCenter
            Layout.topMargin: Tokens.space4
            Layout.bottomMargin: Tokens.space5
            spacing: 6
            visible: root.st === "available" || root.st === "downloading"
            C.IconGlyph {
                anchors.verticalCenter: parent.verticalCenter
                name: "lock"; size: 12
                glyphColor: Theme.on_surface_var
            }
            C.AppText {
                anchors.verticalCenter: parent.verticalCenter
                text: I18n.tr("Your library, keys and settings stay exactly as they are")
                font.pixelSize: Tokens.fontMicro
                color: Theme.on_surface_var
                opacity: 0.85
            }
        }
        Item {
            Layout.fillWidth: true
            implicitHeight: Tokens.space6
            visible: !(root.st === "available" || root.st === "downloading")
        }
    }
}
