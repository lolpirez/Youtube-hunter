import QtQuick
import QtQuick.Layouts
import QtQuick.Shapes
import NicheScope
import "." as C
import "../Utils.js" as U

// v1.0.3 "Hunt report" — the after-run summary card, redesigned.
//
// The v16 banner poured the whole status SENTENCE into one bold column (and a
// layout loop squeezed that column to ~200 px), with a ring that misread
// "2 results … (2 of 25 scanned)" as 100 %. This version reads the run as
// DATA (Niche.resultFacts) and gives each fact its own place:
//
//   ┌──────────────────────────────────────────────────────────────────────┐
//   │ (●) HUNT COMPLETE   ⌕ king                  [YouTube ran dry] [⚡]   │
//   │                                                                      │
//   │  2   matches                       ┌ SCANNED   WANTED   HIT RATE ┐   │
//   │      passed every filter           │   25        200       8 %    │   │
//   │                                    └─────────────────────────────┘   │
//   │  ▁▃█▂▁▂▃▁▂▁▃▂▁▂█▁▃▂▁▂▁▃▂▁▂   ← one bar per scanned video, hits lit  │
//   │                                                                      │
//   │  ┌ 💡 That is all YouTube's results page gives for this keyword… ┐   │
//   │  └ ⓘ  …                                                         ┘   │
//   │  🗄 Archived under “king”   +2 new   0 refreshed                     │
//   └──────────────────────────────────────────────────────────────────────┘
//
// Signature: the SCAN STRIP. Every bar is one video the hunt looked at; the
// matches stand up full height in the tone colour. On arrival a scan head
// sweeps across and lights them in order, the counter rolls up, and the hits
// then breathe very slowly so the card feels alive without asking for focus.
//
// Without facts (warnings, errors, Monetize's status, an archive-only note)
// it falls back to the COMPACT layout: icon tile, a short headline, the
// remaining sentences as calm body text, and the note chip.
//
// API (unchanged, plus `facts`):
//   * kind: "ok" | "success" | "warn" | "error" | "info" | "muted"
//   * text: the plain status line (a leading ✓ / ✔ / • is stripped)
//   * facts: Niche.resultFacts — { shown, scanned, total, target, keyword,
//            stop, notes: [{kind, text}] }; empty → compact layout
//   * note / noteIcon / noteKind: the archive line (or any second line)
//   * showRatio: compact layout only — draw a "<a> of <b>" ring
//   * trailing: extra items (e.g. the quota pill)
Rectangle {
    id: root
    property string kind: "info"
    property string text: ""
    property var facts: ({})
    property string note: ""
    property string noteIcon: "archive"
    property string noteKind: "ok"
    property bool showRatio: true
    default property alias trailing: trailSlot.data

    // ── derived state ────────────────────────────────────────────────────
    readonly property bool _good: kind === "ok" || kind === "success"
    readonly property bool rich: _good && !!facts && facts.shown !== undefined
                                 && facts.shown !== null && Number(facts.shown) > 0
    readonly property color tone: _toneOf(kind)

    function _toneOf(k) {
        return k === "error" ? Theme.error
             : k === "warn" ? Theme.warn
             : (k === "ok" || k === "success") ? Theme.secondary_container
             : k === "muted" ? Theme.outline
             : Theme.primary
    }
    function _noteTone(k) {
        return k === "tip" ? Theme.tertiary
             : (k === "warn" || k === "key") ? Theme.warn
             : k === "quota" ? Theme.primary
             : Theme.on_surface_var
    }
    function _noteIcon(k) {
        return k === "tip" ? "lightbulb"
             : k === "warn" ? "warning"
             : k === "key" ? "vpn_key"
             : k === "quota" ? "bolt"
             : k === "translate" ? "translate"
             : k === "audience" ? "user"
             : "info"
    }
    function _fmt(n) {
        n = Math.round(Number(n) || 0)
        return n.toString().replace(/\B(?=(\d{3})+(?!\d))/g, ",")
    }

    readonly property int _shown: rich ? Number(facts.shown) : 0
    readonly property int _scanned: rich ? Math.max(_shown, Number(facts.scanned || facts.total || 0)) : 0
    readonly property int _target: rich ? Number(facts.target || 0) : 0
    readonly property real _rate: _scanned > 0 ? _shown / _scanned : 0
    readonly property string _rateText: _rate <= 0 ? "0%"
                                      : (_rate < 0.01 ? "<1%" : Math.round(_rate * 100) + "%")
    readonly property var _notes: rich && facts.notes ? facts.notes : []
    readonly property string _keyword: rich && facts.keyword ? String(facts.keyword) : ""
    readonly property string _stop: rich && facts.stop ? String(facts.stop) : ""
    readonly property string _stopLabel: {
        if (!rich) return ""
        if (_target > 0 && _shown >= _target) return "Target hit"
        return _stop === "scrape" ? "YouTube ran dry"
             : _stop === "end" ? "No more results"
             : _stop === "budget" ? "Dig budget spent"
             : _stop === "quota" ? "Quota spent"
             : _stop === "hopeless" ? "Filters too tight"
             : _stop === "cache" ? "From cache"
             : ""
    }
    readonly property color _stopTone: (_target > 0 && _shown >= _target) ? tone
                                     : _stop === "cache" ? Theme.on_surface_var
                                     : Theme.warn

    // compact layout: headline = first segment, the rest = body sentences
    readonly property string _clean: (text || "").replace(/^[\s✓✔•●]+/, "")
    readonly property var _segments: {
        var t = _clean.trim()
        if (t === "") return []
        var parts = t.split(/\s{2,}/)
        if (parts.length === 1) {
            // one long sentence run: cut after the first full stop
            var m = t.match(/^(.+?[.!?])\s+(\S.*)$/)
            if (m && m[1].length >= 18) parts = [m[1], m[2]]
        }
        return parts
    }
    readonly property string headline: _segments.length ? _segments[0].replace(/\.\s*$/, "") : ""
    readonly property string _body: _segments.length > 1 ? _segments.slice(1).join(" ") : ""

    // compact ring: only an explicit "<a> of <b>"
    readonly property var _nums: {
        var m = _clean.match(/(\d[\d,]*)\s+of\s+(\d[\d,]*)/)
        if (!m) return null
        var a = parseInt(m[1].replace(/,/g, "")), b = parseInt(m[2].replace(/,/g, ""))
        if (!(b > 0) || a > b) return null
        return { a: a, b: b }
    }
    readonly property bool _ratio: !rich && showRatio && _good && _nums !== null
    readonly property real _frac: _ratio ? _nums.a / _nums.b : 0

    // archive line, parsed into parts when it has the usual shape
    readonly property var _arch: {
        var m = (note || "").match(/^Archived under\s+(.+?)\s+·\s+(\d[\d,]*)\s+new,\s+(\d[\d,]*)\s+refreshed/)
        return m ? { kw: m[1], added: m[2], upd: m[3] } : null
    }
    readonly property color _noteTint: _toneOf(noteKind)

    // ── motion ───────────────────────────────────────────────────────────
    property real _in: 1        // card arrival
    property real _sweep: 1     // scan head, 0 → 1 across the strip
    property real _count: 1     // counter roll-up
    property real _breath: 0    // ambient pulse of the hits
    readonly property bool _alive: visible && Tokens.appActive && !Tokens.themeSwapping

    readonly property string sigKey: rich ? (_shown + "/" + _scanned + "/" + _target + "/" + _keyword)
                                        : headline
    onSigKeyChanged: if (sigKey !== "") enter.restart()

    ParallelAnimation {
        id: enter
        NumberAnimation { target: root; property: "_in"; from: 0; to: 1; duration: 560; easing.type: Easing.OutCubic }
        SequentialAnimation {
            PauseAnimation { duration: 140 }
            NumberAnimation { target: root; property: "_sweep"; from: 0; to: 1; duration: 1150; easing.type: Easing.OutCubic }
        }
        NumberAnimation { target: root; property: "_count"; from: 0; to: 1; duration: 950; easing.type: Easing.OutExpo }
    }
    SequentialAnimation on _breath {
        running: root.rich && root._alive && root._sweep >= 1
        loops: Animation.Infinite
        NumberAnimation { from: 0; to: 1; duration: 1800; easing.type: Easing.InOutSine }
        NumberAnimation { from: 1; to: 0; duration: 1800; easing.type: Easing.InOutSine }
    }

    opacity: 0.25 + 0.75 * _in
    transform: Translate { y: (1 - root._in) * 10 }

    // ── card ─────────────────────────────────────────────────────────────
    readonly property int _pad: rich ? 20 : 14
    Layout.fillWidth: true
    implicitHeight: (rich ? richCol.implicitHeight : compact.implicitHeight) + _pad * 2
    radius: rich ? Tokens.radiusXl : Tokens.radiusLg + 1
    antialiasing: true
    color: Theme.isDark ? U.mix(Theme.surface_low, Theme.background, 0.2) : Theme.panel_bg
    border.width: 1
    border.color: U.mix(Theme.border, tone, rich ? 0.38 : 0.28)
    clip: true

    // tone light falling in from the top corner (rich) / the reading edge (compact)
    C.CornerGlow {
        visible: root.rich
        radius: root.radius
        size: 560
        glow: root.tone
        strength: Theme.isDark ? 0.20 : 0.13
    }
    Rectangle {
        visible: !root.rich
        anchors.fill: parent
        radius: parent.radius
        rotation: I18n.rtl ? 180 : 0
        gradient: Gradient {
            orientation: Gradient.Horizontal
            GradientStop { position: 0.0; color: U.withAlpha(root.tone, Theme.isDark ? 0.14 : 0.08) }
            GradientStop { position: 0.55; color: U.withAlpha(root.tone, 0.0) }
        }
    }
    // specular top edge
    Rectangle {
        anchors.top: parent.top
        anchors.topMargin: 1
        anchors.horizontalCenter: parent.horizontalCenter
        width: parent.width - 40
        height: 1
        color: Theme.glass_hi
    }

    // the trailing items live in whichever layout is showing
    Row {
        id: trailSlot
        parent: root.rich ? richTrail : compactTrail
        anchors.verticalCenter: parent ? parent.verticalCenter : undefined
        spacing: Tokens.space2
    }

    // ══════════════════════════ RICH: the hunt report ══════════════════════
    Column {
        id: richCol
        visible: root.rich
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.margins: root._pad
        spacing: 18

        // ── header: state · keyword · stop reason · trailing ──
        RowLayout {
            width: parent.width
            spacing: Tokens.space2

            // live dot with a slow ping
            Item {
                Layout.preferredWidth: 14
                Layout.preferredHeight: 14
                Layout.alignment: Qt.AlignVCenter
                Rectangle {
                    anchors.centerIn: parent
                    width: 8; height: 8; radius: 4
                    color: root.tone
                }
                Rectangle {
                    id: ping
                    anchors.centerIn: parent
                    width: 8; height: 8; radius: 4
                    color: "transparent"
                    border.width: 1.5
                    border.color: root.tone
                    property real p: 0
                    scale: 1 + 1.6 * p
                    opacity: 0.7 * (1 - p)
                    NumberAnimation on p {
                        running: root.rich && root._alive
                        loops: Animation.Infinite
                        from: 0; to: 1; duration: 2200; easing.type: Easing.OutCubic
                    }
                }
            }
            C.AppText {
                Layout.alignment: Qt.AlignVCenter
                text: "HUNT COMPLETE"
                weight: Tokens.weightBold
                font.pixelSize: Tokens.fontMicro
                font.letterSpacing: I18n.rtl ? 0 : 1.4
                color: U.mix(root.tone, Theme.text_strong, Theme.isDark ? 0.15 : 0.25)
            }
            // keyword chip
            Rectangle {
                visible: root._keyword !== ""
                Layout.alignment: Qt.AlignVCenter
                Layout.leftMargin: Tokens.space1
                Layout.maximumWidth: 260
                implicitWidth: kwRow.implicitWidth + 18
                implicitHeight: 24
                radius: Tokens.radiusPill
                color: U.withAlpha(Theme.on_surface_var, Theme.isDark ? 0.10 : 0.07)
                border.width: 1
                border.color: U.withAlpha(Theme.on_surface_var, 0.16)
                Row {
                    id: kwRow
                    anchors.centerIn: parent
                    spacing: 5
                    C.IconGlyph {
                        name: "search"; size: 12
                        glyphColor: Theme.on_surface_var
                        anchors.verticalCenter: parent.verticalCenter
                    }
                    C.AppText {
                        text: root._keyword
                        width: Math.min(implicitWidth, 220)
                        elide: Text.ElideRight
                        weight: Tokens.weightDemiBold
                        font.pixelSize: Tokens.fontCaption
                        color: Theme.on_surface
                        anchors.verticalCenter: parent.verticalCenter
                    }
                }
            }
            Item { Layout.fillWidth: true; implicitHeight: 1 }
            // why the dig stopped (or that it hit the target)
            Rectangle {
                visible: root._stopLabel !== ""
                Layout.alignment: Qt.AlignVCenter
                implicitWidth: stopTxt.implicitWidth + 20
                implicitHeight: 24
                radius: Tokens.radiusPill
                color: U.withAlpha(root._stopTone, Theme.isDark ? 0.13 : 0.10)
                border.width: 1
                border.color: U.withAlpha(root._stopTone, 0.30)
                C.AppText {
                    id: stopTxt
                    anchors.centerIn: parent
                    text: root._stopLabel
                    weight: Tokens.weightDemiBold
                    font.pixelSize: Tokens.fontCaption
                    color: U.mix(root._stopTone, Theme.text_strong, Theme.isDark ? 0.2 : 0.3)
                }
            }
            Item {
                id: richTrail
                Layout.alignment: Qt.AlignVCenter
                implicitWidth: trailSlot.parent === richTrail ? trailSlot.implicitWidth : 0
                implicitHeight: trailSlot.parent === richTrail ? trailSlot.implicitHeight : 0
                visible: implicitWidth > 0
            }
        }

        // ── hero: the count, and the run in three numbers ──
        GridLayout {
            id: hero
            width: parent.width
            readonly property bool wide: width >= 600
            columns: wide ? 3 : 1
            columnSpacing: Tokens.space4
            rowSpacing: Tokens.space3

            RowLayout {
                Layout.alignment: Qt.AlignVCenter
                spacing: 14
                C.AppText {
                    Layout.alignment: Qt.AlignVCenter
                    text: root._fmt(root._shown * root._count)
                    weight: Tokens.weightExtraBold
                    font.pixelSize: 56
                    font.letterSpacing: -2
                    font.features: ({ "tnum": 1 })
                    lineHeight: 0.9
                    color: Theme.text_strong
                    LayoutMirroring.enabled: false
                }
                Column {
                    Layout.alignment: Qt.AlignVCenter
                    spacing: 2
                    C.AppText {
                        text: root._shown === 1 ? "match" : "matches"
                        weight: Tokens.weightBold
                        font.pixelSize: Tokens.fontSubtitle + 1
                        color: Theme.text_strong
                    }
                    C.AppText {
                        text: "passed every filter"
                        weight: Tokens.weightMedium
                        font.pixelSize: Tokens.fontCaption + 1
                        color: Theme.on_surface_var
                    }
                }
            }

            Item { visible: hero.wide; Layout.fillWidth: true; implicitHeight: 1 }

            // stat tray
            Rectangle {
                Layout.alignment: hero.wide ? (Qt.AlignVCenter | Qt.AlignRight) : Qt.AlignLeft
                implicitWidth: stats.implicitWidth + 32
                implicitHeight: stats.implicitHeight + 22
                radius: Tokens.radiusLg
                color: U.withAlpha(root.tone, Theme.isDark ? 0.07 : 0.05)
                border.width: 1
                border.color: U.withAlpha(root.tone, Theme.isDark ? 0.18 : 0.16)
                Row {
                    id: stats
                    anchors.centerIn: parent
                    spacing: 26
                    Repeater {
                        model: {
                            var out = [{ k: "SCANNED", v: root._fmt(root._scanned) }]
                            if (root._target > 0)
                                out.push({ k: "WANTED", v: root._fmt(root._target) })
                            out.push({ k: "HIT RATE", v: root._rateText, hot: true })
                            return out
                        }
                        Column {
                            required property var modelData
                            spacing: 3
                            C.AppText {
                                text: modelData.k
                                weight: Tokens.weightBold
                                font.pixelSize: Tokens.fontMicro
                                font.letterSpacing: I18n.rtl ? 0 : 1.1
                                color: Theme.on_surface_var
                            }
                            C.AppText {
                                text: modelData.v
                                mono: true
                                weight: Tokens.weightBold
                                font.pixelSize: 20
                                font.features: ({ "tnum": 1 })
                                color: modelData.hot ? U.mix(root.tone, Theme.text_strong, Theme.isDark ? 0.1 : 0.2)
                                                     : Theme.text_strong
                                LayoutMirroring.enabled: false
                            }
                        }
                    }
                }
            }
        }

        // ── signature: the scan strip ──
        Item {
            id: strip
            width: parent.width
            height: 38
            readonly property int maxBars: Math.max(8, Math.min(110, Math.floor(width / 8)))
            readonly property bool bucketed: root._scanned > maxBars
            readonly property int n: Math.max(1, Math.min(root._scanned, maxBars))
            readonly property int lit: root._shown <= 0 ? 0
                                     : (!bucketed ? Math.min(root._shown, n)
                                                  : Math.max(1, Math.round(root._rate * n)))
            // slim bars; a short scan spreads them out instead of fattening them
            readonly property real barW: Math.max(2, Math.min(8, (width - 3 * (n - 1)) / n))
            readonly property real gap: n > 1 ? (width - barW * n) / (n - 1) : 0

            Repeater {
                model: strip.n
                Rectangle {
                    id: bar
                    required property int index
                    // hits spread evenly along the run, like finds along a scan
                    readonly property bool hit: strip.lit > 0
                        && Math.floor((index + 1) * strip.lit / strip.n + 0.5) > Math.floor(index * strip.lit / strip.n + 0.5)
                    readonly property real seed: {
                        var v = Math.sin((index + 1) * 12.9898) * 43758.5453
                        return v - Math.floor(v)
                    }
                    readonly property real reveal: Math.max(0, Math.min(1,
                        (root._sweep * (strip.n + 6) - index) / 6))
                    readonly property real rest: hit ? 1.0 : (0.20 + 0.32 * seed)
                    x: index * (strip.barW + strip.gap)
                    width: strip.barW
                    height: strip.height * (hit ? (0.30 + 0.70 * reveal) : rest * (0.55 + 0.45 * reveal))
                    y: (strip.height - height) / 2
                    radius: Math.min(width / 2, 3)
                    color: hit && reveal > 0.3 ? root.tone
                                               : U.withAlpha(Theme.on_surface_var, Theme.isDark ? 0.22 : 0.20)
                    opacity: hit ? (0.86 + 0.14 * root._breath) : (0.45 + 0.55 * reveal)
                    // soft halo behind a hit
                    Rectangle {
                        visible: bar.hit && bar.reveal > 0.3
                        z: -1
                        anchors.centerIn: parent
                        width: parent.width + 10
                        height: parent.height + 10
                        radius: width / 2
                        color: U.withAlpha(root.tone, (Theme.isDark ? 0.20 : 0.14) * (0.6 + 0.4 * root._breath))
                    }
                }
            }
            // the scan head, only while sweeping
            Rectangle {
                visible: root._sweep > 0 && root._sweep < 1
                x: root._sweep * strip.width - width / 2
                y: -4
                width: 2
                height: strip.height + 8
                radius: 1
                color: root.tone
                opacity: Math.min(1, (1 - root._sweep) * 4)
                Rectangle {
                    anchors.right: parent.left
                    anchors.verticalCenter: parent.verticalCenter
                    width: 46
                    height: parent.height
                    gradient: Gradient {
                        orientation: Gradient.Horizontal
                        GradientStop { position: 0.0; color: U.withAlpha(root.tone, 0.0) }
                        GradientStop { position: 1.0; color: U.withAlpha(root.tone, 0.22) }
                    }
                }
            }
        }

        // ── insights: what the run wants you to know ──
        Rectangle {
            visible: root._notes.length > 0
            width: parent.width
            height: visible ? notesCol.implicitHeight + 24 : 0
            radius: Tokens.radiusLg
            color: U.withAlpha(Theme.on_surface_var, Theme.isDark ? 0.06 : 0.045)
            border.width: 1
            border.color: U.withAlpha(Theme.on_surface_var, 0.10)
            Column {
                id: notesCol
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                anchors.leftMargin: 12
                anchors.rightMargin: 14
                spacing: 10
                Repeater {
                    model: root._notes
                    RowLayout {
                        required property var modelData
                        required property int index
                        width: notesCol.width
                        spacing: 10
                        readonly property color nt: root._noteTone(modelData.kind)
                        opacity: Math.max(0, Math.min(1, root._sweep * 3 - 0.6 - index * 0.25))
                        Rectangle {
                            Layout.alignment: Qt.AlignTop
                            Layout.preferredWidth: 24
                            Layout.preferredHeight: 24
                            radius: 8
                            color: U.withAlpha(parent.nt, Theme.isDark ? 0.15 : 0.11)
                            C.IconGlyph {
                                anchors.centerIn: parent
                                name: root._noteIcon(modelData.kind)
                                fill: true
                                size: 13
                                glyphColor: parent.parent.nt
                            }
                        }
                        C.AppText {
                            Layout.fillWidth: true
                            Layout.alignment: Qt.AlignVCenter
                            text: modelData.text
                            wrapMode: Text.Wrap
                            weight: modelData.kind === "tip" ? Tokens.weightMedium : Tokens.weightRegular
                            font.pixelSize: Tokens.fontBody - 1
                            lineHeight: 1.25
                            color: modelData.kind === "tip" ? Theme.on_surface : Theme.on_surface_var
                        }
                    }
                }
            }
        }

        // ── archive receipt ──
        RowLayout {
            visible: root.note !== ""
            width: parent.width
            spacing: Tokens.space2
            opacity: Math.max(0, Math.min(1, root._sweep * 2 - 0.8))
            C.IconGlyph {
                Layout.alignment: Qt.AlignVCenter
                name: root.noteIcon; fill: true; size: 14
                glyphColor: root._noteTint
            }
            C.AppText {
                Layout.alignment: Qt.AlignVCenter
                Layout.fillWidth: root._arch === null
                text: root._arch ? "Archived under " + root._arch.kw : root.note
                wrapMode: root._arch ? Text.NoWrap : Text.Wrap
                elide: root._arch ? Text.ElideRight : Text.ElideNone
                Layout.maximumWidth: root._arch ? 320 : -1
                weight: Tokens.weightDemiBold
                font.pixelSize: Tokens.fontCaption
                color: root._arch ? Theme.on_surface : U.mix(root._noteTint, Theme.text_strong, 0.25)
            }
            Repeater {
                model: root._arch ? [
                    { t: "+" + root._arch.added + " " + "new", hot: root._arch.added !== "0" },
                    { t: root._arch.upd + " " + "refreshed", hot: false }
                ] : []
                Rectangle {
                    required property var modelData
                    Layout.alignment: Qt.AlignVCenter
                    implicitWidth: pillTxt.implicitWidth + 16
                    implicitHeight: 22
                    radius: Tokens.radiusPill
                    readonly property color pc: modelData.hot ? root._noteTint : Theme.on_surface_var
                    color: U.withAlpha(pc, Theme.isDark ? 0.12 : 0.09)
                    C.AppText {
                        id: pillTxt
                        anchors.centerIn: parent
                        text: modelData.t
                        mono: true
                        weight: Tokens.weightBold
                        font.pixelSize: Tokens.fontMicro
                        color: modelData.hot ? U.mix(parent.pc, Theme.text_strong, Theme.isDark ? 0.15 : 0.25)
                                             : Theme.on_surface_var
                    }
                }
            }
            Item { Layout.fillWidth: root._arch !== null; implicitHeight: 1 }
        }
    }

    // ══════════════════════════ COMPACT: everything else ═══════════════════
    RowLayout {
        id: compact
        visible: !root.rich
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.verticalCenter: parent.verticalCenter
        anchors.leftMargin: root._pad
        anchors.rightMargin: root._pad
        spacing: Tokens.space3

        Rectangle {
            Layout.alignment: (root._body !== "" || root.note !== "") ? Qt.AlignTop : Qt.AlignVCenter
            Layout.preferredWidth: 38
            Layout.preferredHeight: 38
            radius: 12
            color: U.withAlpha(root.tone, Theme.isDark ? 0.16 : 0.12)
            border.width: 1
            border.color: U.withAlpha(root.tone, 0.32)
            scale: 0.6 + 0.4 * root._in
            C.IconGlyph {
                anchors.centerIn: parent
                name: root.kind === "error" ? "error"
                    : root.kind === "warn" ? "warning"
                    : root._good ? "check_circle" : "info"
                fill: true
                size: 19
                glyphColor: root.tone
            }
        }

        Column {
            id: compactText
            Layout.fillWidth: true
            Layout.alignment: Qt.AlignVCenter
            spacing: 6

            C.AppText {
                width: compactText.width
                visible: text !== ""
                text: root.headline
                weight: Tokens.weightBold
                font.pixelSize: Tokens.fontBody + 1
                color: Theme.text_strong
                wrapMode: Text.Wrap
            }
            C.AppText {
                width: compactText.width
                visible: text !== ""
                text: root._body
                font.pixelSize: Tokens.fontBody - 1
                lineHeight: 1.25
                color: Theme.on_surface_var
                wrapMode: Text.Wrap
            }
            Item { width: 1; height: root.note !== "" ? 2 : 0; visible: root.note !== "" }
            // note chip
            Rectangle {
                id: noteChip
                visible: root.note !== ""
                width: Math.min(compactText.width, noteRow.implicitWidth + 20)
                height: noteTxt.implicitHeight + 10
                radius: 9
                color: U.withAlpha(root._noteTint, Theme.isDark ? 0.10 : 0.07)
                border.width: 1
                border.color: U.withAlpha(root._noteTint, 0.24)
                Row {
                    id: noteRow
                    anchors.left: parent.left
                    anchors.leftMargin: 10
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: 7
                    C.IconGlyph {
                        name: root.noteIcon; fill: true; size: 13
                        glyphColor: root._noteTint
                        anchors.verticalCenter: parent.verticalCenter
                    }
                    C.AppText {
                        id: noteTxt
                        width: Math.min(implicitWidth, compactText.width - 40)
                        text: root.note
                        weight: Tokens.weightMedium
                        font.pixelSize: Tokens.fontCaption
                        color: U.mix(root._noteTint, Theme.text_strong, Theme.isDark ? 0.35 : 0.2)
                        wrapMode: Text.Wrap
                        anchors.verticalCenter: parent.verticalCenter
                    }
                }
            }
        }

        // match ring (explicit "<a> of <b>" only)
        Item {
            id: ringBox
            visible: root._ratio
            Layout.alignment: Qt.AlignVCenter
            Layout.preferredWidth: 46
            Layout.preferredHeight: 46
            property real sweep: 360 * root._frac * root._in
            Shape {
                anchors.fill: parent
                antialiasing: true
                ShapePath {
                    strokeColor: U.withAlpha(root.tone, 0.16)
                    strokeWidth: 4
                    fillColor: "transparent"
                    PathAngleArc { centerX: 23; centerY: 23; radiusX: 19; radiusY: 19; startAngle: 0; sweepAngle: 360 }
                }
                ShapePath {
                    strokeColor: root.tone
                    strokeWidth: 4
                    fillColor: "transparent"
                    capStyle: ShapePath.RoundCap
                    PathAngleArc { centerX: 23; centerY: 23; radiusX: 19; radiusY: 19; startAngle: -90; sweepAngle: ringBox.sweep }
                }
            }
            C.AppText {
                anchors.centerIn: parent
                text: Math.round(root._frac * 100) + "%"
                mono: true
                weight: Tokens.weightBold
                font.pixelSize: 11
                color: root.tone
                LayoutMirroring.enabled: false
            }
        }

        Item {
            id: compactTrail
            Layout.alignment: Qt.AlignVCenter
            implicitWidth: trailSlot.parent === compactTrail ? trailSlot.implicitWidth : 0
            implicitHeight: trailSlot.parent === compactTrail ? trailSlot.implicitHeight : 0
            visible: implicitWidth > 0
        }
    }
}
