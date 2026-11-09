import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import QtQuick.Dialogs
import NicheScope
import "." as C
import "../Utils.js" as U

// v1.3.3 "Post-production deck": captions (style, language) and AI dub picked
// INLINE, right where the download / cut is started. One component for the
// download quality picker and the Video Cutter inspector.
//
// Why it exists: in 1.3.x both places only had a teaser card whose buttons
// opened the full Voice Studio sheet, and the Cutter's quick switch defaulted
// to "translate into Persian", so styling YouTube's OWN captions always asked
// for an AI provider. Here:
//   * Language defaults to "Original (no AI)": pick a look and go.
//   * Picking a language first uses YouTube's own track in it (an uploaded
//     track always; YouTube's free auto-translate when you choose it). The AI
//     is only asked when YouTube has nothing in that language, and the status
//     line says which of the three will happen BEFORE anything runs.
//   * AI dub sits next to captions, with the choices that matter (language,
//     voices, what happens to the original audio, quality).
//
// Lean on purpose: no layers, no shader effects, no running timers. The look
// tiles are static miniatures; the only timer is a one-shot that defers the
// YouTube track read + AI re-check until after the host's entrance animation.
//
// Choices are queued in VoiceStudio (setQueued / clearQueued) and run by
// themselves once the file exists: downloads_vm / cutter_vm call runQueued().
ColumnLayout {
    id: deck
    spacing: Tokens.space3

    // ── what the choices are for ──
    property string videoId: ""          // YouTube id ("" for a local file)
    property string path: ""             // local file (Cutter file mode)
    property string title: ""
    property real clipStart: 0
    property real clipEnd: 0
    property bool busy: false
    property bool audioOnly: false
    property string runsAfterText: I18n.tr("runs after the download")
    // draw our own header (the Cutter's InspectorCard already has one)
    property bool showHeader: true

    signal openStudio(int tab)
    signal settingsRequested()

    // ── queue state (source of truth) ──
    readonly property var q: { var _r = VoiceStudio.queueRev; return VoiceStudio.queuedFor(videoId, path) }
    readonly property bool capOn: q.captions !== undefined
    readonly property bool dubOn: q.dub !== undefined
    readonly property bool anyOn: capOn || dubOn
    property int lane: 0                 // 0 captions, 1 dub: whose settings show

    // ── YouTube tracks for this video ──
    readonly property var tk: { var _r = VoiceStudio.tracksRev; return VoiceStudio.tracksFor(videoId) }

    // ── caption choices ──
    property string capSource: "youtube" // youtube | file | ai
    property int capTrack: 0             // 0 = best match, n = tk.tracks[n - 1]
    property string subPath: ""
    property string capLang: ""          // "" = original language, no AI
    property string capVia: "ai"         // ai | youtube (free auto-translate)
    property string capStyle: "youtube"
    property bool styleTouched: false    // v1.4: the user picked a look themselves
    property int capYt: 0                // v1.4: index into tk.languages (YouTube source)
    property string capSize: "m"
    property string capPos: "bottom"
    property string capOut: "burn"       // burn | soft | srt

    // ── dub choices ──
    property string dubLang: "fa"
    property string dubVoices: "auto"    // auto | single
    property int dubVoice: 3             // Kore
    property string dubBg: "duck"        // duck | bed | replace
    property int dubModel: 0
    property bool dubSubs: false

    // ── derived: will this need the AI? ──
    function _base(c) { return String(c || "").replace("-orig", "").split("-")[0].toLowerCase() }
    readonly property string _lb: _base(capLang)
    readonly property var _pickedTrack: (capSource === "youtube" && capTrack > 0 && tk.tracks) ? (tk.tracks[capTrack - 1] || null) : null
    readonly property string _srcLang: _pickedTrack ? _base(_pickedTrack.lang) : _base(tk.spoken)
    readonly property bool _ytAuto: capSource === "youtube" && capTrack === 0
    readonly property bool _ytUploaded: _ytAuto && _lb !== "" && (tk.uploaded || []).indexOf(_lb) >= 0
    readonly property bool _ytCanTranslate: _ytAuto && _lb !== "" && (tk.translatable || []).indexOf(_lb) >= 0
    readonly property bool _sameLang: _lb !== "" && _srcLang !== "" && _lb === _srcLang
    // v1.4: YouTube = the video's own captions, exactly as YouTube serves
    // them, in any language YouTube itself offers. Never an AI. Every other
    // language lives under "AI listens".
    readonly property var _ytLangs: tk.languages || []
    readonly property var _ytPick: capSource === "youtube" && _ytLangs.length > 0
                                   ? (_ytLangs[Math.min(capYt, _ytLangs.length - 1)] || null) : null
    readonly property bool _capNeedsAi: capSource !== "youtube" && _lb !== "" && !_sameLang
    readonly property bool _rtl: ["fa", "ar", "ur", "ps", "ku", "he"].indexOf(
        capSource === "youtube" ? (_ytPick ? _ytPick.lang : _srcLang) : (_lb !== "" ? _lb : _srcLang)) >= 0

    readonly property var _sources: videoId !== ""
        ? [ { key: "youtube", label: I18n.tr("YouTube"), icon: "youtube" },
            { key: "file", label: I18n.tr("My file"), icon: "description" },
            { key: "ai", label: I18n.tr("AI listens"), icon: "gemini" } ]
        : [ { key: "file", label: I18n.tr("My file"), icon: "description" },
            { key: "ai", label: I18n.tr("AI listens"), icon: "gemini" } ]
    function _sourceIndex() {
        for (var i = 0; i < _sources.length; i++) if (_sources[i].key === capSource) return i
        return 0
    }
    function _styleName(key) {
        var st = VoiceStudio.styles
        for (var i = 0; i < st.length; i++) if (st[i].key === key) return st[i].name
        return key
    }
    function _fileName(p) {
        if (!p) return ""
        try { return decodeURIComponent(String(p).split(/[\\/]/).pop()) } catch (e) { return String(p).split(/[\\/]/).pop() }
    }

    // ── queue writers ──
    function _srcOpts() {
        var o = { source: capSource, track: "", sub_path: "" }
        if (capSource === "youtube" && _ytPick) o.track = _ytPick.key
        else if (capSource === "youtube" && _pickedTrack) o.track = _pickedTrack.key
        if (capSource === "file") {
            if (subPath !== "") o.sub_path = subPath
            else o.source = ""           // let the studio pick the file next to the video
        }
        return o
    }
    function pushCaptions() {
        var o = _srcOpts()
        if (capSource === "youtube") {
            o.target = _ytPick ? _ytPick.lang : ""
            o.allow_ai = false
            o.translate_via = "youtube"
        } else {
            o.target = capLang
            o.translate_via = "ai"
        }
        o.style = capStyle
        o.size = capSize
        o.position = capPos
        o.bilingual = false
        o.output = audioOnly ? "srt" : capOut
        o.clip_start = clipStart
        o.clip_end = clipEnd
        VoiceStudio.setQueued("captions", videoId, path, title, o)
    }
    function pushDub() {
        var o = _srcOpts()
        o.track = ""                     // the dub always listens to the spoken track
        var v = VoiceStudio.voices[dubVoice]
        var m = VoiceStudio.ttsModels[dubModel]
        o.target = dubLang
        o.voice_mode = dubVoices
        o.voice = v ? v.name : "Kore"
        o.acting = true
        o.bg_mode = dubBg
        o.tts_model = m ? m.key : ""
        o.keep_original = true
        o.burn_style = (dubSubs && !audioOnly) ? capStyle : ""
        o.size = capSize
        o.position = capPos
        o.clip_start = clipStart
        o.clip_end = clipEnd
        VoiceStudio.setQueued("dub", videoId, path, title, o)
    }
    function _sync() {
        if (capOn) pushCaptions()
        if (dubOn) pushDub()
    }
    function setCaptions(on) {
        if (on) { pushCaptions(); lane = 0 }
        else VoiceStudio.clearQueued("captions", videoId, path)
    }
    function setDub(on) {
        if (on) { pushDub(); lane = 1 }
        else VoiceStudio.clearQueued("dub", videoId, path)
    }

    // new video / file: start from its natural source, read its tracks later
    function _setSource(k) {
        capSource = k
        if (!styleTouched) capStyle = k === "youtube" ? "youtube" : "pop"
    }
    function _retarget() {
        _setSource(VoiceStudio.defaultSource(videoId, path))
        capTrack = 0
        capYt = 0
        subPath = ""
        lane = dubOn && !capOn ? 1 : 0
        warm.restart()
    }
    onVideoIdChanged: _retarget()
    onPathChanged: _retarget()
    Component.onCompleted: _retarget()
    onVisibleChanged: if (visible) warm.restart()
    onAudioOnlyChanged: if (capOn) pushCaptions()

    // one-shot, after the host's entrance: tracks + "is an AI set up?"
    Timer {
        id: warm
        interval: Tokens.durPage + 160
        repeat: false
        onTriggered: {
            if (!deck.visible) return
            if (deck.videoId !== "") VoiceStudio.loadTracks(deck.videoId)
            VoiceStudio.refreshReadiness()
        }
    }

    FileDialog {
        id: subDialog
        title: I18n.tr("Choose a subtitle file")
        fileMode: FileDialog.OpenFile
        nameFilters: [I18n.tr("Subtitles") + " (*.srt *.vtt *.ass *.ssa)", I18n.tr("All files") + " (*)"]
        onAccepted: { deck.subPath = selectedFile.toString(); deck.capSource = "file"; deck._sync() }
    }

    // ═════════════ header ═════════════
    RowLayout {
        Layout.fillWidth: true
        visible: deck.showHeader
        spacing: Tokens.space2
        C.IconGlyph { name: "auto_awesome"; fill: true; size: 15; glyphColor: deck.anyOn ? Theme.primary : Theme.on_surface_var }
        C.AppText {
            text: I18n.tr("Captions & AI dub")
            weight: Tokens.weightBold
            font.pixelSize: Tokens.fontBody
            color: Theme.text_strong
        }
        Item { Layout.fillWidth: true }
        StatusPill {
            on: deck.anyOn
            text: deck.anyOn ? deck.runsAfterText : I18n.tr("optional")
        }
    }

    // ═════════════ the two lanes ═════════════
    RowLayout {
        Layout.fillWidth: true
        spacing: Tokens.space2

        LaneTile {
            Layout.fillWidth: true
            Layout.preferredWidth: 1
            enabled: !deck.busy
            glyph: "subject"
            heading: I18n.tr("Captions")
            on: deck.capOn
            focused: deck.lane === 0
            summary: deck.capOn
                     ? ((deck.capSource === "youtube"
                         ? (deck._ytPick ? VoiceStudio.languageName(deck._ytPick.lang) : I18n.tr("YouTube"))
                         : deck.capLang === "" ? I18n.tr("Original") : VoiceStudio.languageName(deck.capLang))
                        + " · " + (deck.audioOnly ? ".srt" : deck._styleName(deck.capStyle)))
                     : I18n.tr("Style the words on screen")
            onPicked: { deck.lane = 0; if (!deck.capOn) deck.setCaptions(true) }
            onSwitched: (v) => deck.setCaptions(v)
        }
        LaneTile {
            Layout.fillWidth: true
            Layout.preferredWidth: 1
            enabled: !deck.busy
            glyph: "volume_up"
            heading: I18n.tr("AI dub")
            on: deck.dubOn
            focused: deck.lane === 1
            flag: VoiceStudio.geminiReady ? "" : I18n.tr("Gemini")
            summary: deck.dubOn
                     ? (VoiceStudio.languageName(deck.dubLang) + " · "
                        + (deck.dubVoices === "auto" ? I18n.tr("Auto-cast") : I18n.tr("One voice")))
                     : I18n.tr("A new voice, another language")
            onPicked: { deck.lane = 1; if (!deck.dubOn) deck.setDub(true) }
            onSwitched: (v) => deck.setDub(v)
        }
    }

    // ═════════════ captions settings ═════════════
    Rectangle {
        Layout.fillWidth: true
        visible: deck.lane === 0 && deck.capOn
        radius: Tokens.radiusLg
        color: Theme.surface_low
        border.width: 1
        border.color: Theme.border
        implicitHeight: capGrid.implicitHeight + Tokens.space4 * 2

        GridLayout {
            id: capGrid
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.margins: Tokens.space4
            columns: width >= 420 ? 2 : 1
            columnSpacing: Tokens.space3
            rowSpacing: Tokens.space3

            // ── words from ──
            RowLabel { text: I18n.tr("Words") }
            ColumnLayout {
                Layout.fillWidth: true
                spacing: Tokens.space2
                C.SlideSegment {
                    Layout.fillWidth: true
                    equal: true; solid: false; cellHeight: 32
                    fontSize: Tokens.fontCaption + 1; iconSize: 13
                    enabledCells: !deck.busy
                    model: deck._sources
                    currentIndex: deck._sourceIndex()
                    onActivated: (i) => {
                        var k = deck._sources[i].key
                        if (k === "file" && deck.subPath === "" && deck.path === "") { subDialog.open(); return }
                        deck._setSource(k)
                        deck._sync()
                    }
                }
                // youtube: which track
                C.Dropdown {
                    Layout.fillWidth: true
                    visible: false            // v1.4: the Language list below names the track
                    enabled: !deck.busy
                    popupMaxHeight: 300
                    model: [I18n.tr("Best match (automatic)")].concat(deck.tk.labels || [])
                    currentIndex: deck.capTrack
                    onActivated: { deck.capTrack = currentIndex; deck._sync() }
                }
                Hint {
                    visible: deck.capSource === "youtube"
                    glyph: deck.tk.loading ? "hourglass_empty" : (deck.tk.error ? "info" : "youtube")
                    tone: deck.tk.error && deck.tk.known ? "warn" : ""
                    text: deck.tk.loading ? I18n.tr("Checking which captions YouTube has…")
                        : (deck.tk.error && deck.tk.known) ? I18n.tr("YouTube shows no captions here. Try “AI listens”.")
                        : I18n.tr("YouTube's own captions · no AI, no cost · exact timing")
                    action: (deck.tk.error && deck.tk.known) ? I18n.tr("AI listens") : ""
                    onActed: { deck._setSource("ai"); deck._sync() }
                }
                Hint {
                    visible: deck.capSource === "file"
                    glyph: "description"
                    text: deck.subPath !== "" ? deck._fileName(deck.subPath)
                                              : I18n.tr("The subtitle file next to the video")
                    action: I18n.tr("Choose…")
                    onActed: subDialog.open()
                }
                // v1.4.9: listening runs on the Captions pick in Settings,
                // the same provider + model that translates
                Hint {
                    visible: deck.capSource === "ai"
                    glyph: VoiceStudio.listenReady ? "auto_awesome" : "warning"
                    tone: VoiceStudio.listenReady ? "" : "warn"
                    // v1.4.13: with listening off in Settings (or a pick that
                    // doesn't hear) a YouTube video starts from YouTube's words
                    text: !VoiceStudio.listenReady
                          ? I18n.tr("Pick a provider that can hear audio for Captions")
                          : (!VoiceStudio.listenAudio && deck.videoId !== "")
                            ? I18n.tr("Uses YouTube's captions, the AI makes them fluent. No captions here? It listens instead.")
                          : (VoiceStudio.listenLabel !== ""
                             ? VoiceStudio.listenLabel + " " + I18n.tr("listens and writes the lines")
                             : I18n.tr("Your Captions AI listens and writes the lines"))
                    action: VoiceStudio.listenReady ? "" : I18n.tr("Settings")
                    onActed: deck.settingsRequested()
                }
            }

            // ── language ──
            RowLabel { text: I18n.tr("Language") }
            ColumnLayout {
                Layout.fillWidth: true
                spacing: Tokens.space2
                // YouTube: only the languages YouTube itself has for this video
                C.Dropdown {
                    Layout.fillWidth: true
                    visible: deck.capSource === "youtube"
                    enabled: !deck.busy && deck._ytLangs.length > 0
                    popupMaxHeight: 340
                    model: deck._ytLangs.length > 0 ? deck.tk.langLabels
                           : [deck.tk.loading || !deck.tk.known ? I18n.tr("Reading YouTube's captions…")
                                                                 : I18n.tr("No captions on YouTube")]
                    currentIndex: deck._ytLangs.length > 0 ? Math.min(deck.capYt, deck._ytLangs.length - 1) : 0
                    onActivated: { deck.capYt = currentIndex; deck._sync() }
                }
                // My file / AI listens: every language (AI translates)
                C.Dropdown {
                    Layout.fillWidth: true
                    visible: deck.capSource !== "youtube"
                    enabled: !deck.busy
                    popupMaxHeight: 340
                    model: [I18n.tr("Original · as spoken")].concat(VoiceStudio.languageLabels)
                    currentIndex: deck.capLang === "" ? 0 : VoiceStudio.languageIndex(deck.capLang) + 1
                    onActivated: {
                        deck.capLang = currentIndex <= 0 ? "" : VoiceStudio.languageCode(currentIndex - 1)
                        deck._sync()
                    }
                }
                C.SlideSegment {
                    Layout.fillWidth: true
                    visible: false            // v1.4: YouTube never asks an AI now
                    equal: true; solid: false; cellHeight: 30
                    fontSize: Tokens.fontCaption; iconSize: 12
                    enabledCells: !deck.busy
                    model: [ { label: I18n.tr("YouTube · free"), icon: "youtube" },
                             { label: I18n.tr("AI · natural"), icon: "auto_awesome" } ]
                    currentIndex: deck.capVia === "youtube" ? 0 : 1
                    onActivated: (i) => { deck.capVia = i === 0 ? "youtube" : "ai"; deck._sync() }
                }
                Hint {
                    readonly property string lname: VoiceStudio.languageName(deck.capLang)
                    glyph: deck._capNeedsAi ? (VoiceStudio.aiReady ? "auto_awesome" : "warning") : "check_circle"
                    tone: deck._capNeedsAi ? (VoiceStudio.aiReady ? "" : "warn") : "ok"
                    text: deck.capSource === "youtube"
                          ? (deck._ytPick && deck._ytPick.kind === "translated"
                             ? I18n.tr("YouTube's own free translation. No AI. Need better wording? Use “AI listens”.")
                             : I18n.tr("YouTube's captions, exactly as YouTube shows them. No AI, no cost."))
                          : deck.capLang === ""
                          ? I18n.tr("Styled exactly as spoken. No AI, no cost.")
                          : deck._ytUploaded ? I18n.tr("YouTube has {l} captions: used as-is, no AI.").replace("{l}", lname)
                          : deck._sameLang ? I18n.tr("That's the spoken language: nothing to translate.")
                          : (deck.capVia === "youtube" && deck._ytCanTranslate) ? I18n.tr("YouTube's free auto-translate, no AI.")
                          : VoiceStudio.aiReady ? I18n.tr("AI translates it naturally via {p}.").replace("{p}", VoiceStudio.aiLabel)
                          : I18n.tr("Translating needs an AI provider.")
                    action: deck.capSource === "youtube" ? I18n.tr("Every language")
                          : deck._capNeedsAi && !VoiceStudio.aiReady ? I18n.tr("Settings") : ""
                    onActed: {
                        if (deck.capSource === "youtube") { deck._setSource("ai"); deck._sync() }
                        else deck.settingsRequested()
                    }
                }
            }

            // ── look ──
            RowLabel { text: I18n.tr("Look"); visible: lookGrid.visible }
            GridLayout {
                id: lookGrid
                Layout.fillWidth: true
                visible: !deck.audioOnly && deck.capOut !== "srt"
                columns: capGrid.width >= 470 ? 5 : 3
                columnSpacing: Tokens.space2
                rowSpacing: Tokens.space2
                Repeater {
                    model: VoiceStudio.styles
                    StyleTile {
                        required property var modelData
                        Layout.fillWidth: true
                        Layout.preferredWidth: 1
                        spec: modelData
                        rtl: deck._rtl
                        selected: deck.capStyle === modelData.key
                        enabled: !deck.busy
                        onClicked: { deck.capStyle = modelData.key; deck.styleTouched = true; deck._sync() }
                    }
                }
            }

            // ── size + place ──
            RowLabel { text: I18n.tr("Size · place"); visible: sizeRow.visible }
            RowLayout {
                id: sizeRow
                Layout.fillWidth: true
                visible: lookGrid.visible
                spacing: Tokens.space2
                C.SlideSegment {
                    Layout.fillWidth: true
                    Layout.preferredWidth: 3
                    equal: true; solid: false; cellHeight: 30
                    fontSize: Tokens.fontCaption
                    enabledCells: !deck.busy
                    model: ["S", "M", "L"]
                    currentIndex: ["s", "m", "l"].indexOf(deck.capSize)
                    onActivated: (i) => { deck.capSize = ["s", "m", "l"][i]; deck._sync() }
                }
                C.SlideSegment {
                    Layout.fillWidth: true
                    Layout.preferredWidth: 4
                    equal: true; solid: false; cellHeight: 30
                    fontSize: Tokens.fontCaption
                    enabledCells: !deck.busy
                    model: [I18n.tr("Bottom"), I18n.tr("Middle"), I18n.tr("Top")]
                    currentIndex: ["bottom", "middle", "top"].indexOf(deck.capPos)
                    onActivated: (i) => { deck.capPos = ["bottom", "middle", "top"][i]; deck._sync() }
                }
            }

            // ── output ──
            RowLabel { text: I18n.tr("Output") }
            ColumnLayout {
                Layout.fillWidth: true
                spacing: Tokens.space2
                C.SlideSegment {
                    Layout.fillWidth: true
                    visible: !deck.audioOnly
                    equal: true; solid: false; cellHeight: 30
                    fontSize: Tokens.fontCaption; iconSize: 12
                    enabledCells: !deck.busy
                    model: [ { label: I18n.tr("Burn in"), icon: "movie" },
                             { label: I18n.tr("Track"), icon: "subject" },
                             { label: ".srt", icon: "description" } ]
                    currentIndex: ["burn", "soft", "srt"].indexOf(deck.capOut)
                    onActivated: (i) => { deck.capOut = ["burn", "soft", "srt"][i]; deck._sync() }
                }
                Hint {
                    glyph: "info"
                    text: deck.audioOnly ? I18n.tr("Audio download: captions are saved as an .srt next to it.")
                        : deck.capOut === "burn" ? I18n.tr("Drawn into a copy. The plain file is kept too.")
                        : deck.capOut === "soft" ? I18n.tr("A subtitle track you can switch off. No re-encode.")
                        : I18n.tr("Just the .srt file, for YouTube CC or an editor.")
                }
            }
        }
    }

    // ═════════════ dub settings ═════════════
    Rectangle {
        Layout.fillWidth: true
        visible: deck.lane === 1 && deck.dubOn
        radius: Tokens.radiusLg
        color: Theme.surface_low
        border.width: 1
        border.color: Theme.border
        implicitHeight: dubGrid.implicitHeight + Tokens.space4 * 2

        GridLayout {
            id: dubGrid
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.margins: Tokens.space4
            columns: width >= 420 ? 2 : 1
            columnSpacing: Tokens.space3
            rowSpacing: Tokens.space3

            RowLabel { text: I18n.tr("Into") }
            C.Dropdown {
                Layout.fillWidth: true
                enabled: !deck.busy
                popupMaxHeight: 340
                model: VoiceStudio.languageLabels
                currentIndex: VoiceStudio.languageIndex(deck.dubLang)
                onActivated: { deck.dubLang = VoiceStudio.languageCode(currentIndex); deck._sync() }
            }

            RowLabel { text: I18n.tr("Voices") }
            ColumnLayout {
                Layout.fillWidth: true
                spacing: Tokens.space2
                C.SlideSegment {
                    Layout.fillWidth: true
                    equal: true; solid: false; cellHeight: 30
                    fontSize: Tokens.fontCaption; iconSize: 12
                    enabledCells: !deck.busy
                    model: [ { label: I18n.tr("Auto-cast"), icon: "auto_awesome" },
                             { label: I18n.tr("One voice"), icon: "user" } ]
                    currentIndex: deck.dubVoices === "auto" ? 0 : 1
                    onActivated: (i) => { deck.dubVoices = i === 0 ? "auto" : "single"; deck._sync() }
                }
                C.Dropdown {
                    Layout.fillWidth: true
                    visible: deck.dubVoices === "single"
                    enabled: !deck.busy
                    popupMaxHeight: 300
                    model: VoiceStudio.voiceLabels
                    currentIndex: deck.dubVoice
                    onActivated: { deck.dubVoice = currentIndex; deck._sync() }
                }
            }

            RowLabel { text: I18n.tr("Original") }
            C.SlideSegment {
                Layout.fillWidth: true
                equal: true; solid: false; cellHeight: 30
                fontSize: Tokens.fontCaption
                enabledCells: !deck.busy
                model: [I18n.tr("Ducked"), I18n.tr("Low bed"), I18n.tr("Replace")]
                currentIndex: ["duck", "bed", "replace"].indexOf(deck.dubBg)
                onActivated: (i) => { deck.dubBg = ["duck", "bed", "replace"][i]; deck._sync() }
            }

            RowLabel { text: I18n.tr("Quality"); visible: VoiceStudio.ttsModels.length > 1 }
            C.SlideSegment {
                Layout.fillWidth: true
                visible: VoiceStudio.ttsModels.length > 1
                equal: true; solid: false; cellHeight: 30
                fontSize: Tokens.fontCaption
                enabledCells: !deck.busy
                model: VoiceStudio.ttsModels.map(function(m) { return m.label })
                currentIndex: deck.dubModel
                onActivated: (i) => { deck.dubModel = i; deck._sync() }
            }

            RowLabel { text: I18n.tr("Captions"); visible: !deck.audioOnly }
            RowLayout {
                Layout.fillWidth: true
                visible: !deck.audioOnly
                spacing: Tokens.space2
                C.AppText {
                    Layout.fillWidth: true
                    wrapMode: Text.Wrap
                    text: I18n.tr("Burn the dub's lines in, in the “{s}” look").replace("{s}", deck._styleName(deck.capStyle))
                    font.pixelSize: Tokens.fontCaption
                    color: Theme.on_surface_var
                }
                C.AppSwitch {
                    checked: deck.dubSubs
                    enabled: !deck.busy
                    onToggled: { deck.dubSubs = checked; deck._sync() }
                }
            }

            RowLabel { text: I18n.tr("Listens to") }
            Hint {
                glyph: VoiceStudio.geminiReady ? (deck.capSource === "youtube" ? "youtube" : deck.capSource === "file" ? "description" : "gemini") : "warning"
                tone: VoiceStudio.geminiReady ? "" : "warn"
                text: !VoiceStudio.geminiReady ? I18n.tr("AI dub needs a Google Gemini key (free from Google AI Studio).")
                    : deck.capSource === "youtube" ? I18n.tr("YouTube's captions for timing, Gemini for the voices")
                    : deck.capSource === "file" ? I18n.tr("Your subtitle file for timing, Gemini for the voices")
                    : I18n.tr("Gemini listens and speaks")
                action: VoiceStudio.geminiReady ? "" : I18n.tr("Settings")
                onActed: deck.settingsRequested()
            }
        }
    }

    // ═════════════ footer ═════════════
    RowLayout {
        Layout.fillWidth: true
        spacing: Tokens.space2
        C.AppText {
            Layout.fillWidth: true
            elide: Text.ElideRight
            readonly property string _capLangNow: deck.capSource === "youtube" ? (deck._ytPick ? deck._ytPick.lang : "") : deck._lb
            text: VoiceStudio.runningCount > 0
                  ? I18n.tr("{n} caption / dub job running").replace("{n}", VoiceStudio.runningCount)
                  : (deck.capOn && deck.dubOn && _capLangNow !== "" && _capLangNow === deck._base(deck.dubLang) && deck.capSource !== "youtube")
                  ? I18n.tr("One pass: the dub's lines become the captions. No second translation.")
                  : (deck.capOn && deck.dubOn) ? I18n.tr("One video at the end: dub + captions, one encode.")
                  : deck.anyOn ? I18n.tr("It runs right after, inside the same progress bar.")
                  : I18n.tr("Switch one on. It starts by itself when the file is ready.")
            font.pixelSize: Tokens.fontCaption
            color: VoiceStudio.runningCount > 0 ? Theme.primary : Theme.outline
        }
        C.AppText {
            text: VoiceStudio.runningCount > 0 ? I18n.tr("Progress") : I18n.tr("All options")
            weight: Tokens.weightBold
            font.pixelSize: Tokens.fontCaption
            color: allMa.containsMouse ? Theme.primary_hover : Theme.primary
            MouseArea {
                id: allMa
                anchors.fill: parent
                anchors.margins: -6
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                onClicked: deck.openStudio(deck.lane)
            }
        }
    }

    // ═════════════ parts ═════════════
    component RowLabel: C.AppText {
        Layout.alignment: Qt.AlignTop
        Layout.topMargin: 7
        Layout.preferredWidth: 78
        elide: Text.ElideRight
        weight: Tokens.weightDemiBold
        font.pixelSize: Tokens.fontCaption
        color: Theme.on_surface_var
    }

    component StatusPill: Rectangle {
        id: sp
        property bool on: false
        property string text: ""
        implicitHeight: 24
        implicitWidth: spRow.implicitWidth + Tokens.space3 * 2
        radius: height / 2
        color: on ? U.withAlpha(Theme.primary, 0.12) : Theme.surface_low
        border.width: 1
        border.color: on ? U.withAlpha(Theme.primary, 0.35) : Theme.border
        Row {
            id: spRow
            anchors.centerIn: parent
            spacing: 5
            Rectangle {
                width: 6; height: 6; radius: 3
                anchors.verticalCenter: parent.verticalCenter
                color: sp.on ? Theme.primary : Theme.outline
            }
            C.AppText {
                anchors.verticalCenter: parent.verticalCenter
                text: sp.text
                weight: Tokens.weightDemiBold
                font.pixelSize: Tokens.fontMicro
                color: sp.on ? Theme.primary : Theme.on_surface_var
            }
        }
    }

    // one line of guidance: icon, text, optional link
    component Hint: RowLayout {
        id: hint
        property string glyph: "info"
        property string text: ""
        property string tone: ""         // "" | ok | warn
        property string action: ""
        signal acted()
        Layout.fillWidth: true
        spacing: 6
        readonly property color _c: tone === "warn" ? Theme.warn : tone === "ok" ? Theme.primary : Theme.outline
        C.IconGlyph {
            Layout.alignment: Qt.AlignTop
            Layout.topMargin: 1
            name: hint.glyph; size: 13; fill: true
            glyphColor: hint._c
        }
        C.AppText {
            Layout.fillWidth: true
            wrapMode: Text.Wrap
            text: hint.text
            font.pixelSize: Tokens.fontCaption
            color: hint.tone === "warn" ? Theme.warn : Theme.on_surface_var
        }
        C.AppText {
            visible: hint.action !== ""
            Layout.alignment: Qt.AlignTop
            text: hint.action
            weight: Tokens.weightBold
            font.pixelSize: Tokens.fontCaption
            color: Theme.primary
            MouseArea {
                anchors.fill: parent
                anchors.margins: -6
                cursorShape: Qt.PointingHandCursor
                onClicked: hint.acted()
            }
        }
    }

    // a lane: icon chip, name, one-line summary, switch
    component LaneTile: AbstractButton {
        id: lt
        property string glyph: ""
        property string heading: ""
        property string summary: ""
        property string flag: ""
        property bool on: false
        property bool focused: false
        signal picked()
        signal switched(bool v)
        implicitHeight: 64
        onClicked: lt.picked()
        HoverHandler { cursorShape: Qt.PointingHandCursor }
        background: Rectangle {
            radius: Tokens.radiusLg
            color: lt.on ? U.withAlpha(Theme.primary, Theme.isDark ? 0.12 : 0.07)
                 : lt.hovered ? U.hoverTint(Theme.surface_low, Theme.isDark) : Theme.surface_low
            border.width: lt.on && lt.focused ? 2 : 1
            border.color: lt.on && lt.focused ? Theme.primary
                        : lt.on ? U.withAlpha(Theme.primary, 0.35)
                        : lt.hovered ? U.withAlpha(Theme.primary, 0.30) : Theme.border
            Behavior on border.color { ColorAnimation { duration: Tokens.durFast } }
            // focus notch: which lane's settings are open below
            Rectangle {
                visible: lt.on && lt.focused
                width: 18; height: 3; radius: 1.5
                anchors.horizontalCenter: parent.horizontalCenter
                anchors.bottom: parent.bottom
                anchors.bottomMargin: 4
                color: Theme.primary
            }
        }
        contentItem: RowLayout {
            spacing: Tokens.space3
            Rectangle {
                Layout.leftMargin: Tokens.space3
                Layout.alignment: Qt.AlignVCenter
                width: 36; height: 36; radius: Tokens.radiusMd
                color: lt.on ? Theme.primary : U.withAlpha(Theme.on_surface, Theme.isDark ? 0.08 : 0.06)
                C.IconGlyph {
                    anchors.centerIn: parent
                    name: lt.glyph; fill: true; size: 18
                    glyphColor: lt.on ? Theme.on_primary : Theme.on_surface_var
                }
            }
            ColumnLayout {
                Layout.fillWidth: true
                Layout.minimumWidth: 0
                Layout.preferredWidth: 1
                Layout.alignment: Qt.AlignVCenter
                spacing: 1
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 6
                    C.AppText {
                        Layout.fillWidth: true
                        Layout.minimumWidth: 0
                        elide: Text.ElideRight
                        text: lt.heading
                        weight: Tokens.weightBold
                        font.pixelSize: Tokens.fontBody
                        color: lt.on ? Theme.primary : Theme.text_strong
                    }
                    Rectangle {
                        visible: lt.flag !== ""
                        implicitHeight: 16
                        implicitWidth: flagTxt.implicitWidth + 10
                        radius: 8
                        color: U.withAlpha(Theme.warn, 0.14)
                        C.AppText {
                            id: flagTxt
                            anchors.centerIn: parent
                            text: lt.flag
                            weight: Tokens.weightBold
                            font.pixelSize: 10
                            color: Theme.warn
                        }
                    }
                }
                C.AppText {
                    Layout.fillWidth: true
                    elide: Text.ElideRight
                    text: lt.summary
                    font.pixelSize: Tokens.fontCaption
                    color: Theme.on_surface_var
                }
            }
            C.AppSwitch {
                Layout.rightMargin: Tokens.space3
                Layout.alignment: Qt.AlignVCenter
                Layout.minimumWidth: implicitWidth
                Layout.maximumWidth: implicitWidth
                checked: lt.on
                enabled: lt.enabled
                onToggled: lt.switched(checked)
            }
        }
    }

    // a static miniature of one caption look on a dusk frame
    component StyleTile: AbstractButton {
        id: st
        property var spec: ({})
        property bool rtl: false
        property bool selected: false
        implicitHeight: 74
        HoverHandler { cursorShape: Qt.PointingHandCursor }
        readonly property string _a: rtl ? "اینو" : "watch"
        readonly property string _b: rtl ? "ببین" : "this"
        background: Rectangle {
            radius: Tokens.radiusMd
            gradient: Gradient {
                GradientStop { position: 0.0; color: "#2B3A55" }
                GradientStop { position: 0.6; color: "#4A3F60" }
                GradientStop { position: 1.0; color: "#151B28" }
            }
            border.width: st.selected ? 2 : 1
            border.color: st.selected ? Theme.primary
                        : st.hovered ? U.withAlpha(Theme.primary, 0.5) : U.withAlpha("#FFFFFF", 0.10)
            Behavior on border.color { ColorAnimation { duration: Tokens.durFast } }
        }
        contentItem: Item {
            clip: true
            Rectangle {
                visible: (st.spec.box || "") !== ""
                anchors.centerIn: sample
                width: sample.implicitWidth + 10
                height: sample.implicitHeight + 4
                radius: st.spec.key === "boxed" ? 6 : 3
                color: st.spec.box || "transparent"
            }
            Text {
                id: sample
                anchors.horizontalCenter: parent.horizontalCenter
                anchors.verticalCenter: parent.verticalCenter
                anchors.verticalCenterOffset: -8
                width: Math.min(implicitWidth, parent.width - 8)
                horizontalAlignment: Text.AlignHCenter
                elide: Text.ElideRight
                // v1.4.6: "pill" (Spotlight) needs a span background: rich text
                textFormat: (st.spec.pill || "") !== "" ? Text.RichText : Text.StyledText
                font.family: st.rtl ? "Vazirmatn" : Fonts.body
                font.pixelSize: st.spec.key === "punch" ? 18 : (st.spec.key === "pop" || st.spec.key === "spotlight") ? 15 : 13
                font.weight: st.spec.weight || 600
                font.capitalization: (st.spec.upper && !st.rtl) ? Font.AllUppercase : Font.MixedCase
                color: st.spec.fg || "#FFFFFF"
                style: (st.spec.outline || "") !== "" ? Text.Outline : Text.Normal
                styleColor: st.spec.outline || "#000000"
                text: {
                    var hi = st.spec.hi || ""
                    if ((st.spec.pill || "") !== "")
                        return st._a + " <span style='background-color:" + st.spec.pill + "; color:" + (hi || "#FFFFFF")
                             + "'>&nbsp;" + st._b + "&nbsp;</span>"
                    if (st.spec.single)
                        return "<font color='" + (hi || "#FFFFFF") + "'>" + st._b + "</font>"
                    return hi === "" ? st._a + " " + st._b
                         : st._a + " <font color='" + hi + "'>" + st._b + "</font>"
                }
            }
            Text {
                anchors.horizontalCenter: parent.horizontalCenter
                anchors.bottom: parent.bottom
                anchors.bottomMargin: 6
                text: st.spec.name || ""
                font.family: Fonts.body
                font.pixelSize: Tokens.fontMicro
                font.weight: Font.DemiBold
                color: st.selected ? "#FFFFFF" : Qt.rgba(1, 1, 1, 0.62)
            }
        }
    }
}
