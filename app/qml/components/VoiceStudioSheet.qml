import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import QtQuick.Dialogs
import NicheScope
import "." as C
import "../Utils.js" as U

// v1.3.1 Voice Studio, redesigned as a guided flow.
//
// The old sheet was two tabs over one long scroll: "Words from" (jargon) sat
// above everything, the jobs you just started were at the very bottom, and the
// action lived in a footer you had to discover. First-time users didn't know
// where to start. Now the sheet reads top to bottom like a recipe:
//
//   Header      Voice Studio · file name
//   Activity    running / finished jobs FIRST (what you came back for),
//               with Resume when a job stopped (quota, network)
//   Pick        two big cards: Captions  |  AI dub   (what each one makes)
//   1 Language  translate to / dub into  (one dropdown, "keep original" is an option)
//   2 Look      caption style grid + size/position   |  Voices: auto-cast / one voice, acting
//   3 Output    burn / track / .srt                  |  original audio, captions on top, quality
//   Transcript  where the words come from, auto-picked, folded into one line
//               (YouTube = free + exact, file, AI listens) with "Change"
//   Footer      plain summary + the one action (+ Preview 30 s for a dub)
//
// Same public API as before:  openFor({...}), openQueued({...}), and the
// VoiceStudio view-model is unchanged apart from resume() / queue helpers.
Popup {
    id: root
    modal: true
    focus: true
    parent: Overlay.overlay
    padding: 0
    width: Math.min(640, (parent ? parent.width : 900) - 64)
    height: parent ? parent.height - 24 : 700
    x: parent ? parent.width - width - 12 : 0
    y: 12
    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside

    Overlay.modal: C.DialogScrim { }
    enter: Transition {
        ParallelAnimation {
            NumberAnimation { property: "opacity"; from: 0; to: 1; duration: Tokens.durMed; easing.type: Easing.OutCubic }
            NumberAnimation { property: "x"; from: (root.parent ? root.parent.width : 900); to: (root.parent ? root.parent.width - root.width - 12 : 0); duration: Tokens.durSlow; easing.type: Easing.OutCubic }
        }
    }
    exit: Transition {
        ParallelAnimation {
            NumberAnimation { property: "opacity"; from: 1; to: 0; duration: Tokens.durFast }
            NumberAnimation { property: "x"; to: (root.parent ? root.parent.width : 900); duration: Tokens.durMed; easing.type: Easing.InCubic }
        }
    }

    // ── form state (kept between openings, like a real studio) ──
    property int tab: 0                       // 0 captions, 1 dub
    property string source: "youtube"         // youtube | file | ai
    property int trackIndex: 0
    property string subPath: ""
    property real offset: 0
    property string styleKey: "clean"
    property string sizeKey: "m"
    property string posKey: "bottom"
    property bool bilingual: false
    // v1.3.3: "keep the original language" is the default, so styling
    // YouTube's own captions never asks for an AI provider.
    property bool doTranslate: false
    property string targetCode: "fa"
    property string outputMode: "burn"        // burn | soft | srt
    property string dubTarget: "fa"
    property string voiceMode: "auto"
    property int voiceIndex: 3                // Kore
    property bool acting: true
    property string bgMode: "duck"
    property int ttsIndex: 0
    // v1.6: who speaks this dub. "" follows Settings → Captions & Dub
    property string voiceEngine: ""
    property int elVoiceIndex: -1             // -1 = the main voice from Settings
    readonly property string _engine: voiceEngine !== "" ? voiceEngine : VoiceEngine.engine
    readonly property bool _eleven: _engine === "elevenlabs"
    readonly property bool _geminiKey: String((Settings.aiProfileMap["gemini"] || {}).apiKey || "") !== ""
    readonly property bool _voicesReady: _eleven ? VoiceEngine.hasKey : (_geminiKey || VoiceStudio.geminiReady && VoiceEngine.engine === "gemini")
    readonly property int _elIdx: elVoiceIndex >= 0 ? elVoiceIndex : VoiceEngine.voiceIndex
    property bool dubCaptions: false
    property bool showSource: false
    property bool showAllJobs: false

    readonly property var t: VoiceStudio.target
    readonly property bool _rtlPreview: ["fa", "ar", "ur", "ps", "ku", "he"].indexOf((tab === 1 ? dubTarget : (doTranslate ? targetCode : (VoiceStudio.spokenLang || "en")))) >= 0

    function openFor(o) {
        _open(o, false)
    }
    // Pre-action: choose captions/dubbing now; they run automatically when
    // the download or cut that this page starts has finished.
    function openQueued(o) {
        _open(o, true)
    }
    function _open(o, queued) {
        if (queued) VoiceStudio.openQueued(o.path || "", o.title || "", o.videoId || "", o.clipStart || 0, o.clipEnd || 0)
        else VoiceStudio.open(o.path || "", o.title || "", o.videoId || "", o.clipStart || 0, o.clipEnd || 0)
        if (o.videoId) root.source = "youtube"
        else if (VoiceStudio.sidecarPath !== "") { root.source = "file"; root.subPath = VoiceStudio.sidecarPath }
        else root.source = "ai"
        if (root.source !== "file") root.subPath = VoiceStudio.sidecarPath
        root.trackIndex = 0
        root.offset = 0
        root.showSource = false
        root.showAllJobs = false
        root.voiceEngine = ""        // v1.6: each opening starts on the Settings choice
        root.elVoiceIndex = -1
        if (o.tab !== undefined) root.tab = o.tab
        root.open()
        flick.contentY = 0
    }

    readonly property var _queued: VoiceStudio.queuedKinds

    function _styleName(key) {
        var st = VoiceStudio.styles
        for (var i = 0; i < st.length; i++)
            if (st[i].key === key) return st[i].name
        return key
    }
    function _styleIndex(key) {
        var st = VoiceStudio.styles
        for (var i = 0; i < st.length; i++)
            if (st[i].key === key) return i
        return 0
    }

    function _baseOpts() {
        var tr = VoiceStudio.ytTracks
        return {
            source: root.source,
            track: (root.source === "youtube" && tr.length > 0 && root.trackIndex < tr.length) ? tr[root.trackIndex].key : "",
            sub_path: root.subPath,
            offset: root.offset
        }
    }
    // an uploaded YouTube track already in the target language: use it, no AI
    readonly property var _ytTargetTrack: {
        if (!doTranslate || source !== "youtube") return null
        var want = String(targetCode || "").split("-")[0].toLowerCase()
        var tr = VoiceStudio.ytTracks || []
        for (var i = 0; i < tr.length; i++)
            if (!tr[i].auto && String(tr[i].lang || "").split("-")[0].toLowerCase() === want) return tr[i]
        return null
    }
    function startCaptions() {
        var o = _baseOpts()
        if (root._ytTargetTrack) o.track = root._ytTargetTrack.key
        // v1.4: YouTube's free auto-translate first, AI only when YouTube can't
        o.translate_via = root.source === "youtube" ? "youtube" : "ai"
        o.target = root.doTranslate ? root.targetCode : ""
        o.style = root.styleKey
        o.size = root.sizeKey
        o.position = root.posKey
        o.bilingual = root.doTranslate && root.bilingual
        o.output = VoiceStudio.hasVideo ? root.outputMode : "srt"
        if (VoiceStudio.startCaptions(o) !== "queued") root._revealJobs()
    }
    function startDub(previewSec) {
        var o = _baseOpts()
        o.target = root.dubTarget
        o.voice_mode = root.voiceMode
        o.voice = root._eleven
                  ? (VoiceEngine.voices[root._elIdx] ? VoiceEngine.voices[root._elIdx].id : "")
                  : (VoiceStudio.voices[root.voiceIndex] ? VoiceStudio.voices[root.voiceIndex].name : "Kore")
        o.voice_engine = root._engine
        o.acting = root.acting
        o.bg_mode = root.bgMode
        o.tts_model = VoiceStudio.ttsModels[root.ttsIndex] ? VoiceStudio.ttsModels[root.ttsIndex].key : ""
        o.keep_original = true
        o.burn_style = root.dubCaptions ? root.styleKey : ""
        o.size = root.sizeKey
        o.position = root.posKey
        o.preview_sec = previewSec || 0
        if (VoiceStudio.startDub(o) !== "queued") root._revealJobs()
    }
    // jobs live at the top now: after starting one, scroll there
    function _revealJobs() {
        Qt.callLater(function() { flick.contentY = 0 })
    }

    readonly property bool _sourceOk: source === "youtube"
                                      ? (t.videoId !== "" &&
                                         (VoiceStudio.queueMode || (!VoiceStudio.ytLoading && VoiceStudio.ytTracks.length > 0)))
                                    : source === "file" ? subPath !== ""
                                    : (tab === 1 ? (VoiceStudio.geminiReady || root._geminiKey) : VoiceStudio.listenReady)
    readonly property bool _ready: t.exists === true || VoiceStudio.queueMode
    // a queued choice can be re-set: the newest one wins
    readonly property bool _canCaption: _ready && _sourceOk && (!doTranslate || VoiceStudio.aiReady || _ytTargetTrack !== null)
    readonly property bool _canDub: _ready && _sourceOk && root._voicesReady && VoiceStudio.aiReady
    // Why the main action is dimmed, in words.
    readonly property string _blockReason: {
        if (!root._ready) return ""
        if (root.tab === 0 ? root._canCaption : root._canDub) return ""
        if (root.tab === 0 ? root._queued.captions : root._queued.dub) return ""
        if (root.source === "youtube" && root.t.videoId === "" && !VoiceStudio.queueMode)
            return I18n.tr("This file isn't linked to a YouTube video. Open “Transcript” below and pick “My file” or “AI listens”.")
        if (root.source === "youtube" && !VoiceStudio.ytLoading && VoiceStudio.ytTracks.length === 0 && !VoiceStudio.queueMode)
            return I18n.tr("YouTube has no captions for this video. Open “Transcript” below and pick “AI listens”.")
        if (root.source === "file" && root.subPath === "")
            return I18n.tr("Choose a subtitle file first.")
        if (root.source === "ai" && root.tab === 0 && !VoiceStudio.listenReady)
            return I18n.tr("“AI listens” needs a provider that can hear audio. Pick one in Settings → Captions & Dub.")
        if (root.tab === 1 && !root._voicesReady)
            return root._eleven
                   ? I18n.tr("ElevenLabs voices need your ElevenLabs key, added in Settings → Captions & Dub. Or pick Gemini voices in step 2.")
                   : I18n.tr("Gemini voices need a Google Gemini key (free from Google AI Studio), added in Settings → AI Providers. Or pick ElevenLabs in step 2.")
        if (!VoiceStudio.aiReady)
            return I18n.tr("This needs an AI provider. Add one in Settings → AI Providers.")
        return ""
    }
    readonly property bool _blockIsKey: _blockReason.indexOf("Settings") >= 0

    readonly property string _sourceLine: source === "youtube"
            ? I18n.tr("YouTube's own captions · free · exact timing")
            : source === "file" ? (subPath !== "" ? I18n.tr("Your file · {f}").replace("{f}", subPath.split(/[\\/]/).pop())
                                                  : I18n.tr("Your subtitle file (none chosen yet)"))
            : I18n.tr("AI listens to the audio · uses Gemini")

    readonly property var _sortedJobs: {
        var all = VoiceStudio.jobs || []
        var run = all.filter(function(j) { return j.state === "running" })
        var rest = all.filter(function(j) { return j.state !== "running" })
        return run.concat(rest)
    }
    readonly property var _shownJobs: showAllJobs ? _sortedJobs : _sortedJobs.slice(0, Math.max(2, VoiceStudio.runningCount))

    Connections {
        target: VoiceStudio
        function onToast(msg, kind) { sheetToast.show(msg, kind) }
    }

    FileDialog {
        id: subDialog
        title: I18n.tr("Choose a subtitle file")
        fileMode: FileDialog.OpenFile
        nameFilters: [I18n.tr("Subtitles") + " (*.srt *.vtt *.ass *.ssa)", I18n.tr("All files") + " (*)"]
        onAccepted: {
            root.subPath = VoiceStudio.pathFromUrl(selectedFile.toString())
            root.source = "file"
        }
    }

    background: Rectangle {
        radius: Tokens.radiusXl
        color: Theme.surface
        border.width: 1
        border.color: Theme.border
        antialiasing: true
    }

    contentItem: Item {
    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        // ── header ──
        RowLayout {
            Layout.fillWidth: true
            Layout.margins: Tokens.space5
            Layout.bottomMargin: Tokens.space3
            spacing: Tokens.space3
            C.IconTile { iconName: "translate"; size: 40; solid: true; lit: true }
            ColumnLayout {
                Layout.fillWidth: true
                spacing: 2
                C.AppText {
                    text: I18n.tr("Voice Studio")
                    weight: Tokens.weightBold
                    font.pixelSize: Tokens.fontSubtitle
                    color: Theme.text_strong
                }
                C.AppText {
                    Layout.fillWidth: true
                    text: root.t.title || ""
                    font.pixelSize: Tokens.fontCaption + 1
                    color: Theme.on_surface_var
                    elide: Text.ElideRight
                }
            }
            C.IconButton {
                implicitWidth: 34; implicitHeight: 34
                iconName: "close"
                tip: I18n.tr("Close (jobs keep running)")
                onClicked: root.close()
            }
        }
        Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: Theme.border }

        // ── body ──
        Flickable {
            id: flick
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            contentWidth: width
            contentHeight: body.implicitHeight + Tokens.space5 * 2
            boundsBehavior: Flickable.StopAtBounds
            ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

            ColumnLayout {
                id: body
                x: Tokens.space5
                y: Tokens.space4
                width: flick.width - Tokens.space5 * 2
                spacing: Tokens.space4

                // missing file
                Banner {
                    visible: root.t.exists === false && !VoiceStudio.queueMode
                    tint: Theme.error
                    glyph: "warning"
                    text: I18n.tr("The file couldn't be found. It may have been moved or deleted.")
                }
                // pre-action: nothing exists yet, so the choices are saved
                Banner {
                    visible: VoiceStudio.queueMode
                    tint: Theme.primary
                    glyph: root._queued.captions || root._queued.dub ? "check_circle" : "schedule"
                    text: root._queued.captions && root._queued.dub
                          ? I18n.tr("Captions and AI dub are set. They start by themselves the moment your video is ready. You can close this.")
                          : root._queued.captions ? I18n.tr("Captions are set. They start by themselves the moment your video is ready. You can close this.")
                          : root._queued.dub ? I18n.tr("AI dub is set. It starts by itself the moment your video is ready. You can close this.")
                          : I18n.tr("No need to wait: set it up now and it starts by itself the moment your video is ready.")
                }

                // ═══ activity ═══
                ColumnLayout {
                    visible: root._sortedJobs.length > 0
                    Layout.fillWidth: true
                    spacing: Tokens.space2
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: Tokens.space2
                        C.AppText {
                            text: I18n.tr("Activity")
                            weight: Tokens.weightBold
                            font.pixelSize: Tokens.fontBody
                            color: Theme.text_strong
                        }
                        Rectangle {
                            visible: VoiceStudio.runningCount > 0
                            implicitWidth: runLbl.implicitWidth + 14
                            implicitHeight: 20
                            radius: 10
                            color: U.withAlpha(Theme.primary, 0.14)
                            C.AppText {
                                id: runLbl
                                anchors.centerIn: parent
                                text: I18n.tr("{n} running").replace("{n}", VoiceStudio.runningCount)
                                font.pixelSize: Tokens.fontMicro
                                weight: Tokens.weightBold
                                color: Theme.primary
                            }
                        }
                        Item { Layout.fillWidth: true }
                        C.PrimaryButton {
                            visible: root._sortedJobs.length > root._shownJobs.length || root.showAllJobs
                            variant: "ghost"
                            implicitHeight: 28
                            text: root.showAllJobs ? I18n.tr("Show less") : I18n.tr("Show all ({n})").replace("{n}", root._sortedJobs.length)
                            onClicked: root.showAllJobs = !root.showAllJobs
                        }
                        C.PrimaryButton {
                            visible: root._sortedJobs.length > VoiceStudio.runningCount
                            variant: "ghost"
                            implicitHeight: 28
                            text: I18n.tr("Clear finished")
                            onClicked: VoiceStudio.clearFinished()
                        }
                    }
                    Repeater {
                        model: root._shownJobs
                        JobCard {
                            required property var modelData
                            Layout.fillWidth: true
                            job: modelData
                        }
                    }
                }

                // ═══ pick what to make ═══
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: Tokens.space2
                    C.AppText {
                        text: I18n.tr("What do you want to make?")
                        weight: Tokens.weightBold
                        font.pixelSize: Tokens.fontBody
                        color: Theme.text_strong
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: Tokens.space3
                        ModeCard {
                            Layout.fillWidth: true
                            Layout.preferredWidth: 1
                            glyph: "subject"
                            title: I18n.tr("Captions")
                            desc: I18n.tr("Subtitles on the video, in its language or translated")
                            badge: root.source === "youtube" ? I18n.tr("Free with YouTube captions") : I18n.tr("Uses AI")
                            selected: root.tab === 0
                            done: VoiceStudio.queueMode && root._queued.captions
                            onPicked: root.tab = 0
                        }
                        ModeCard {
                            Layout.fillWidth: true
                            Layout.preferredWidth: 1
                            glyph: "volume_up"
                            title: I18n.tr("AI dub")
                            desc: I18n.tr("A new voice in another language, acted like the original")
                            badge: root._eleven ? I18n.tr("ElevenLabs voices") : I18n.tr("Gemini voices")
                            selected: root.tab === 1
                            done: VoiceStudio.queueMode && root._queued.dub
                            onPicked: root.tab = 1
                        }
                    }
                }

                Banner {
                    visible: root.tab === 1 && !root._voicesReady
                    tint: Theme.warn
                    glyph: "key"
                    text: root._eleven
                          ? I18n.tr("ElevenLabs voices are picked, but there is no ElevenLabs key yet. Add it in Settings → Captions & Dub, or pick Gemini voices in step 2.")
                          : I18n.tr("Gemini voices need a Google Gemini key (free from Google AI Studio) in Settings → AI Providers. Or pick ElevenLabs voices in step 2.")
                }

                // ═══ CAPTIONS ═══
                StepCard {
                    visible: root.tab === 0
                    num: 1
                    title: I18n.tr("Language")
                    summary: root.doTranslate ? VoiceStudio.languageName(root.targetCode) : I18n.tr("Original")
                    C.Dropdown {
                        Layout.fillWidth: true
                        popupMaxHeight: 340
                        model: [I18n.tr("Keep the original language")].concat(VoiceStudio.languageLabels)
                        currentIndex: root.doTranslate ? VoiceStudio.languageIndex(root.targetCode) + 1 : 0
                        onActivated: {
                            if (currentIndex <= 0) { root.doTranslate = false }
                            else { root.doTranslate = true; root.targetCode = VoiceStudio.languageCode(currentIndex - 1) }
                        }
                    }
                    Hint {
                        visible: root.doTranslate
                        text: root._ytTargetTrack ? I18n.tr("YouTube already has captions in this language: used as-is, no AI.")
                              : VoiceStudio.aiReady ? I18n.tr("Natural, context-aware translation via {p}.").replace("{p}", VoiceStudio.aiLabel)
                                                  : I18n.tr("Add an AI provider in Settings to translate.")
                        color: (VoiceStudio.aiReady || root._ytTargetTrack) ? Theme.on_surface_var : Theme.warn
                    }
                    ToggleRow {
                        visible: root.doTranslate
                        text: I18n.tr("Bilingual: original line small above")
                        checked: root.bilingual
                        onToggled: (on) => root.bilingual = on
                    }
                }

                StepCard {
                    visible: root.tab === 0
                    num: 2
                    title: I18n.tr("Look")
                    summary: root._styleName(root.styleKey)
                    StyleGrid { Layout.fillWidth: true }
                    SizePos { Layout.fillWidth: true }
                }

                StepCard {
                    visible: root.tab === 0 && VoiceStudio.hasVideo
                    num: 3
                    title: I18n.tr("Output")
                    summary: root.outputMode === "burn" ? I18n.tr("Burn in") : root.outputMode === "soft" ? I18n.tr("Subtitle track") : I18n.tr(".srt only")
                    C.SlideSegment {
                        Layout.fillWidth: true
                        equal: true; solid: false; cellHeight: 32
                        fontSize: Tokens.fontCaption + 1
                        model: [ { label: I18n.tr("Burn in"), icon: "movie" },
                                 { label: I18n.tr("Subtitle track"), icon: "subject" },
                                 { label: I18n.tr(".srt only"), icon: "description" } ]
                        currentIndex: ["burn", "soft", "srt"].indexOf(root.outputMode)
                        onActivated: (i) => root.outputMode = ["burn", "soft", "srt"][i]
                    }
                    Hint {
                        text: root.outputMode === "burn" ? I18n.tr("Drawn into the picture: looks the same everywhere (Instagram, TikTok, Telegram).")
                             : root.outputMode === "soft" ? I18n.tr("A track viewers switch on and off. No re-encode, very fast.")
                             : I18n.tr("Just the .srt files, saved next to the video.")
                    }
                }

                // ═══ DUB ═══
                StepCard {
                    visible: root.tab === 1
                    num: 1
                    title: I18n.tr("Dub into")
                    summary: VoiceStudio.languageName(root.dubTarget)
                    C.Dropdown {
                        Layout.fillWidth: true
                        popupMaxHeight: 340
                        model: VoiceStudio.languageLabels
                        currentIndex: VoiceStudio.languageIndex(root.dubTarget)
                        onActivated: root.dubTarget = VoiceStudio.languageCode(currentIndex)
                    }
                }
                StepCard {
                    visible: root.tab === 1
                    num: 2
                    title: I18n.tr("Voices")
                    summary: (root._eleven ? "ElevenLabs" : "Gemini") + " · "
                             + (root.voiceMode === "auto" ? I18n.tr("Auto-cast")
                                : root._eleven ? (VoiceEngine.voices[root._elIdx] ? VoiceEngine.voices[root._elIdx].label.split(" · ")[0] : "")
                                : (VoiceStudio.voices[root.voiceIndex] ? VoiceStudio.voices[root.voiceIndex].name : ""))
                    // v1.6: who speaks, per dub (starts on the Settings choice)
                    FieldLabel { text: I18n.tr("Who speaks") }
                    C.SlideSegment {
                        Layout.fillWidth: true
                        equal: true; solid: false; cellHeight: 32
                        fontSize: Tokens.fontCaption + 1
                        model: [ { label: I18n.tr("Gemini voices"), icon: "provider_gemini" },
                                 { label: I18n.tr("ElevenLabs"), icon: "volume_up" } ]
                        currentIndex: root._eleven ? 1 : 0
                        onActivated: (i) => root.voiceEngine = i === 1 ? "elevenlabs" : "gemini"
                    }
                    Hint {
                        text: root._eleven
                              ? (VoiceEngine.hasKey ? I18n.tr("ElevenLabs credits, not Gemini requests. Gemini only listens when YouTube has no captions, and adapts the script.")
                                                    : I18n.tr("No ElevenLabs key yet: add it in Settings → Captions & Dub."))
                              : I18n.tr("Uses Gemini requests for every voice take (the quota saver keeps it to about one per speaker).")
                    }
                    FieldLabel { text: I18n.tr("Cast"); Layout.topMargin: Tokens.space1 }
                    C.SlideSegment {
                        Layout.fillWidth: true
                        equal: true; solid: false; cellHeight: 32
                        fontSize: Tokens.fontCaption + 1
                        model: [ { label: I18n.tr("Auto-cast"), icon: "auto_awesome" },
                                 { label: I18n.tr("One voice"), icon: "user" } ]
                        currentIndex: root.voiceMode === "auto" ? 0 : 1
                        onActivated: (i) => root.voiceMode = i === 0 ? "auto" : "single"
                    }
                    Hint {
                        text: root.voiceMode === "auto" ? I18n.tr("Each speaker gets their own voice: a woman a female voice, a man a male one, heard from the voice itself.")
                                                        : I18n.tr("One voice reads every line.")
                    }
                    C.Dropdown {
                        visible: root.voiceMode === "single" && !root._eleven
                        Layout.fillWidth: true
                        popupMaxHeight: 340
                        model: VoiceStudio.voiceLabels
                        currentIndex: root.voiceIndex
                        onActivated: root.voiceIndex = currentIndex
                    }
                    C.Dropdown {
                        visible: root.voiceMode === "single" && root._eleven
                        Layout.fillWidth: true
                        popupMaxHeight: 340
                        model: VoiceEngine.voices.map(function (v) { return v.label })
                        currentIndex: root._elIdx
                        onActivated: root.elVoiceIndex = currentIndex
                    }
                    ToggleRow {
                        text: I18n.tr("Act it out: laughs, sighs, energy and pace from the original")
                        checked: root.acting
                        onToggled: (on) => root.acting = on
                    }
                }
                StepCard {
                    visible: root.tab === 1
                    num: 3
                    title: I18n.tr("Sound & finish")
                    summary: (root.bgMode === "duck" ? I18n.tr("Duck") : root.bgMode === "bed" ? I18n.tr("Low bed") : I18n.tr("Replace"))
                             + " · " + (root._eleven ? (VoiceEngine.models[VoiceEngine.modelIndex] || {}).label || "ElevenLabs"
                                        : (VoiceStudio.ttsModels[root.ttsIndex] ? VoiceStudio.ttsModels[root.ttsIndex].label : ""))
                    FieldLabel { text: I18n.tr("Original audio") }
                    C.SlideSegment {
                        Layout.fillWidth: true
                        equal: true; solid: false; cellHeight: 32
                        fontSize: Tokens.fontCaption + 1
                        model: [I18n.tr("Duck under dub"), I18n.tr("Low bed"), I18n.tr("Replace")]
                        currentIndex: ["duck", "bed", "replace"].indexOf(root.bgMode)
                        onActivated: (i) => root.bgMode = ["duck", "bed", "replace"][i]
                    }
                    Hint {
                        text: root.bgMode === "duck" ? I18n.tr("Music and room sound stay; the original voice dips under the dub.")
                             : root.bgMode === "bed" ? I18n.tr("The original stays quietly underneath the whole time.")
                             : I18n.tr("Only the dub. The original is still kept as a second audio track.")
                    }
                    FieldLabel { text: I18n.tr("Voice quality"); Layout.topMargin: Tokens.space1 }
                    Hint {
                        visible: root._eleven
                        text: I18n.tr("ElevenLabs model: {m}. Change it in Settings → Captions & Dub.")
                              .replace("{m}", (VoiceEngine.models[VoiceEngine.modelIndex] || {}).label || "")
                    }
                    C.SlideSegment {
                        visible: !root._eleven
                        Layout.fillWidth: true
                        equal: true; solid: false; cellHeight: 32
                        fontSize: Tokens.fontCaption + 1
                        model: VoiceStudio.ttsModels.map(function(m) { return m.label + " · " + m.desc })
                        currentIndex: root.ttsIndex
                        onActivated: (i) => root.ttsIndex = i
                    }
                    ToggleRow {
                        visible: VoiceStudio.hasVideo
                        text: I18n.tr("Also draw captions of the dub")
                        checked: root.dubCaptions
                        onToggled: (on) => root.dubCaptions = on
                    }
                    C.Dropdown {
                        visible: VoiceStudio.hasVideo && root.dubCaptions
                        Layout.fillWidth: true
                        model: VoiceStudio.styles.map(function(s) { return s.name + " · " + s.desc })
                        currentIndex: root._styleIndex(root.styleKey)
                        onActivated: root.styleKey = VoiceStudio.styles[currentIndex].key
                    }
                }

                // ═══ transcript (folded) ═══
                Rectangle {
                    Layout.fillWidth: true
                    implicitHeight: srcCol.implicitHeight + Tokens.space3 * 2
                    radius: Tokens.radiusLg
                    color: Theme.surface_low
                    border.width: 1
                    border.color: root._blockReason !== "" && !root._blockIsKey ? U.withAlpha(Theme.warn, 0.55) : Theme.border
                    ColumnLayout {
                        id: srcCol
                        anchors.left: parent.left; anchors.right: parent.right
                        anchors.top: parent.top
                        anchors.margins: Tokens.space3
                        spacing: Tokens.space2
                        RowLayout {
                            Layout.fillWidth: true
                            spacing: Tokens.space2
                            C.IconGlyph {
                                name: root.source === "youtube" ? "youtube" : root.source === "file" ? "upload_file" : "sparkling"
                                fill: true; size: 16
                                glyphColor: Theme.primary
                                Layout.alignment: Qt.AlignVCenter
                            }
                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 1
                                C.AppText {
                                    text: I18n.tr("Transcript")
                                    weight: Tokens.weightBold
                                    font.pixelSize: Tokens.fontCaption + 1
                                    color: Theme.text_strong
                                }
                                C.AppText {
                                    Layout.fillWidth: true
                                    text: root._sourceLine
                                    elide: Text.ElideRight
                                    font.pixelSize: Tokens.fontCaption + 1
                                    color: Theme.on_surface_var
                                }
                            }
                            C.SpinnerGlyph {
                                visible: VoiceStudio.ytLoading && root.source === "youtube"
                                name: "autorenew"; spinning: visible
                                Layout.alignment: Qt.AlignVCenter
                            }
                            C.PrimaryButton {
                                variant: "ghost"
                                implicitHeight: 30
                                iconName: root.showSource ? "expand_less" : "expand_more"
                                text: root.showSource ? I18n.tr("Done") : I18n.tr("Change")
                                onClicked: root.showSource = !root.showSource
                            }
                        }
                        ColumnLayout {
                            visible: root.showSource
                            Layout.fillWidth: true
                            spacing: Tokens.space2
                            C.SlideSegment {
                                Layout.fillWidth: true
                                equal: true
                                solid: false
                                cellHeight: 32
                                fontSize: Tokens.fontCaption + 1
                                model: [ { label: I18n.tr("YouTube"), icon: "youtube" },
                                         { label: I18n.tr("My file"), icon: "upload_file" },
                                         { label: I18n.tr("AI listens"), icon: "sparkling" } ]
                                currentIndex: ["youtube", "file", "ai"].indexOf(root.source)
                                onActivated: (i) => {
                                    root.source = ["youtube", "file", "ai"][i]
                                    if (root.source === "file" && root.subPath === "") subDialog.open()
                                }
                            }
                            Hint {
                                text: root.source === "youtube" ? I18n.tr("Best choice when it's available: exact timing and no AI cost.")
                                     : root.source === "file" ? I18n.tr("An .srt / .vtt / .ass file you already have.")
                                     : I18n.tr("Your Captions AI listens to the audio and writes timed lines. Use it when YouTube has no captions.")
                            }
                            // YouTube track
                            RowLayout {
                                visible: root.source === "youtube"
                                Layout.fillWidth: true
                                spacing: Tokens.space2
                                C.Dropdown {
                                    Layout.fillWidth: true
                                    visible: VoiceStudio.ytTracks.length > 0
                                    model: VoiceStudio.ytTrackLabels
                                    currentIndex: root.trackIndex
                                    onActivated: root.trackIndex = currentIndex
                                }
                                C.AppText {
                                    visible: VoiceStudio.ytLoading
                                    text: I18n.tr("Reading the video's captions…")
                                    color: Theme.on_surface_var
                                    font.pixelSize: Tokens.fontCaption + 1
                                }
                                C.AppText {
                                    visible: !VoiceStudio.ytLoading && VoiceStudio.ytTracks.length === 0
                                    Layout.fillWidth: true
                                    wrapMode: Text.Wrap
                                    text: VoiceStudio.queueMode && root.t.videoId !== ""
                                          ? I18n.tr("We'll take this video's captions when it's ready.")
                                          : root.t.videoId === "" ? I18n.tr("This file isn't linked to a YouTube video.")
                                                                  : (VoiceStudio.ytError || I18n.tr("No captions on YouTube for this video."))
                                    color: VoiceStudio.queueMode && root.t.videoId !== "" ? Theme.on_surface_var : Theme.warn
                                    font.pixelSize: Tokens.fontCaption + 1
                                }
                            }
                            // file
                            RowLayout {
                                visible: root.source === "file"
                                Layout.fillWidth: true
                                spacing: Tokens.space2
                                C.AppText {
                                    Layout.fillWidth: true
                                    text: root.subPath !== "" ? root.subPath.split(/[\\/]/).pop() : I18n.tr("No file chosen")
                                    color: root.subPath !== "" ? Theme.on_surface : Theme.outline
                                    elide: Text.ElideMiddle
                                    font.pixelSize: Tokens.fontBody
                                }
                                C.PrimaryButton {
                                    variant: "ghost"
                                    iconName: "folder_open"
                                    text: I18n.tr("Choose…")
                                    implicitHeight: 32
                                    onClicked: subDialog.open()
                                }
                            }
                            Banner {
                                visible: root.source === "ai" && root.tab === 0 && !VoiceStudio.listenReady
                                tint: Theme.warn
                                glyph: "key"
                                text: I18n.tr("“AI listens” needs a provider that can hear audio. Pick one in Settings → Captions & Dub.")
                            }
                            // clip offset (a stream-copy cut starts on a keyframe)
                            RowLayout {
                                visible: VoiceStudio.isClip && root.source === "youtube"
                                Layout.fillWidth: true
                                spacing: Tokens.space2
                                C.IconGlyph { name: "schedule"; size: 15; glyphColor: Theme.on_surface_var }
                                C.AppText {
                                    Layout.fillWidth: true
                                    wrapMode: Text.Wrap
                                    text: I18n.tr("Captions early or late on this clip? Nudge them.")
                                    color: Theme.on_surface_var
                                    font.pixelSize: Tokens.fontCaption + 1
                                }
                                C.IconButton { implicitWidth: 30; implicitHeight: 30; iconName: "remove"; tip: I18n.tr("Earlier"); onClicked: root.offset = Math.round((root.offset - 0.25) * 100) / 100 }
                                C.AppText {
                                    mono: true
                                    text: (root.offset > 0 ? "+" : "") + root.offset.toFixed(2) + " s"
                                    color: Theme.text_strong
                                    font.pixelSize: Tokens.fontCaption + 1
                                    Layout.preferredWidth: 64
                                    horizontalAlignment: Text.AlignHCenter
                                }
                                C.IconButton { implicitWidth: 30; implicitHeight: 30; iconName: "add"; tip: I18n.tr("Later"); onClicked: root.offset = Math.round((root.offset + 0.25) * 100) / 100 }
                            }
                        }
                    }
                }
            }
        }

        // ── footer ──
        Rectangle {
            Layout.fillWidth: true
            implicitHeight: 1
            color: Theme.border
        }
        RowLayout {
            visible: root._blockReason !== ""
            Layout.fillWidth: true
            Layout.leftMargin: Tokens.space4
            Layout.rightMargin: Tokens.space4
            Layout.topMargin: Tokens.space3
            spacing: Tokens.space2
            C.IconGlyph {
                name: root._blockIsKey ? "key" : "info"
                size: 14
                glyphColor: Theme.warn
                Layout.alignment: Qt.AlignVCenter
            }
            C.AppText {
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                text: root._blockReason
                font.pixelSize: Tokens.fontCaption
                color: Theme.warn
            }
            C.PrimaryButton {
                variant: "ghost"
                text: root._blockIsKey ? I18n.tr("Settings") : I18n.tr("Fix it")
                implicitHeight: 32
                onClicked: {
                    if (root._blockIsKey) { root.close(); App.goto(10) }
                    else { root.showSource = true; flick.contentY = Math.max(0, flick.contentHeight - flick.height) }
                }
            }
        }
        RowLayout {
            Layout.fillWidth: true
            Layout.margins: Tokens.space4
            spacing: Tokens.space2
            ColumnLayout {
                Layout.fillWidth: true
                spacing: 1
                C.AppText {
                    Layout.fillWidth: true
                    elide: Text.ElideRight
                    weight: Tokens.weightDemiBold
                    font.pixelSize: Tokens.fontCaption + 1
                    color: Theme.text_strong
                    text: root.tab === 0
                          ? [root.doTranslate ? VoiceStudio.languageName(root.targetCode) : I18n.tr("Original language"),
                             root._styleName(root.styleKey),
                             root.outputMode === "burn" ? I18n.tr("Burn in") : root.outputMode === "soft" ? I18n.tr("Subtitle track") : I18n.tr(".srt only")].join(" · ")
                          : [VoiceStudio.languageName(root.dubTarget),
                             root.voiceMode === "auto" ? I18n.tr("Auto-cast") : I18n.tr("One voice"),
                             root._eleven ? "ElevenLabs"
                                          : (VoiceStudio.ttsModels[root.ttsIndex] ? VoiceStudio.ttsModels[root.ttsIndex].label : "")].join(" · ")
                }
                C.AppText {
                    Layout.fillWidth: true
                    elide: Text.ElideRight
                    font.pixelSize: Tokens.fontCaption
                    color: Theme.on_surface_var
                    text: VoiceStudio.queueMode ? I18n.tr("Starts by itself after the download or cut.")
                          : root.tab === 1 ? I18n.tr("Not sure about the voices? Preview 30 s first, it costs a fraction.")
                          : I18n.tr("Saved next to the video.")
                }
            }
            C.PrimaryButton {
                visible: root.tab === 1 && !VoiceStudio.queueMode
                variant: "ghost"
                iconName: "play_arrow"
                text: I18n.tr("Preview 30 s")
                enabled: root._canDub
                onClicked: root.startDub(30)
            }
            C.PrimaryButton {
                iconName: (root.tab === 0 ? root._queued.captions : root._queued.dub) && VoiceStudio.queueMode
                          ? "check" : (root.tab === 0 ? "subject" : "volume_up")
                text: root.tab === 0
                      ? (VoiceStudio.queueMode ? (root._queued.captions ? I18n.tr("Update captions") : I18n.tr("Set captions"))
                                               : I18n.tr("Create captions"))
                      : (VoiceStudio.queueMode ? (root._queued.dub ? I18n.tr("Update AI dub") : I18n.tr("Set AI dub"))
                                               : I18n.tr("Dub the video"))
                enabled: root.tab === 0 ? root._canCaption : root._canDub
                onClicked: root.tab === 0 ? root.startCaptions() : root.startDub(0)
            }
        }
    }
    C.Toast { id: sheetToast; anchors.horizontalCenter: parent.horizontalCenter; y: parent.height - 130 }
    }

    // ─────────────────────────── pieces ───────────────────────────
    component FieldLabel: C.AppText {
        font.pixelSize: Tokens.fontCaption + 1
        weight: Tokens.weightDemiBold
        color: Theme.on_surface_var
    }

    component Hint: C.AppText {
        Layout.fillWidth: true
        wrapMode: Text.Wrap
        font.pixelSize: Tokens.fontCaption + 1
        color: Theme.on_surface_var
    }

    // One step of the recipe: number, title, the current choice on the right.
    component StepCard: Rectangle {
        id: stc
        property int num: 1
        property string title: ""
        property string summary: ""
        default property alias items: stBody.data
        Layout.fillWidth: true
        implicitHeight: stCol.implicitHeight + Tokens.space4 * 2
        radius: Tokens.radiusLg
        color: Theme.surface_low
        border.width: 1
        border.color: Theme.border
        ColumnLayout {
            id: stCol
            anchors.left: parent.left; anchors.right: parent.right
            anchors.top: parent.top
            anchors.margins: Tokens.space4
            spacing: Tokens.space3
            RowLayout {
                Layout.fillWidth: true
                spacing: Tokens.space2
                Rectangle {
                    implicitWidth: 22; implicitHeight: 22; radius: 11
                    color: U.withAlpha(Theme.primary, 0.14)
                    border.width: 1
                    border.color: U.withAlpha(Theme.primary, 0.4)
                    C.AppText {
                        anchors.centerIn: parent
                        text: stc.num
                        mono: true
                        weight: Tokens.weightBold
                        font.pixelSize: Tokens.fontMicro
                        color: Theme.primary
                    }
                }
                C.AppText {
                    text: stc.title
                    weight: Tokens.weightBold
                    font.pixelSize: Tokens.fontBody
                    color: Theme.text_strong
                }
                Item { Layout.fillWidth: true }
                C.AppText {
                    Layout.maximumWidth: stc.width * 0.5
                    text: stc.summary
                    elide: Text.ElideRight
                    font.pixelSize: Tokens.fontCaption + 1
                    color: Theme.primary
                    weight: Tokens.weightDemiBold
                }
            }
            ColumnLayout {
                id: stBody
                Layout.fillWidth: true
                spacing: Tokens.space2
            }
        }
    }

    // Big "what do you want" choice.
    component ModeCard: Rectangle {
        id: mc
        property string glyph: ""
        property string title: ""
        property string desc: ""
        property string badge: ""
        property bool selected: false
        property bool done: false
        signal picked()
        implicitHeight: mcCol.implicitHeight + Tokens.space3 * 2
        radius: Tokens.radiusLg
        color: mc.selected ? U.withAlpha(Theme.primary, Theme.isDark ? 0.12 : 0.08)
             : mcHover.hovered ? U.hoverTint(Theme.surface_low, Theme.isDark) : Theme.surface_low
        border.width: mc.selected ? 2 : 1
        border.color: mc.selected ? Theme.primary : (mcHover.hovered ? U.withAlpha(Theme.primary, 0.35) : Theme.border)
        Behavior on color { ColorAnimation { duration: Tokens.durFast } }
        HoverHandler { id: mcHover; cursorShape: Qt.PointingHandCursor }
        TapHandler { onTapped: mc.picked() }
        ColumnLayout {
            id: mcCol
            anchors.left: parent.left; anchors.right: parent.right
            anchors.top: parent.top
            anchors.margins: Tokens.space3
            spacing: Tokens.space2
            RowLayout {
                Layout.fillWidth: true
                spacing: Tokens.space2
                C.IconTile { iconName: mc.glyph; size: 30; solid: mc.selected; lit: mc.selected }
                C.AppText {
                    Layout.fillWidth: true
                    text: mc.title
                    weight: Tokens.weightBold
                    font.pixelSize: Tokens.fontLabel
                    color: mc.selected ? Theme.primary : Theme.text_strong
                }
                C.IconGlyph {
                    visible: mc.selected || mc.done
                    name: "check_circle"; fill: true; size: 18
                    glyphColor: Theme.primary
                }
            }
            C.AppText {
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                text: mc.desc
                font.pixelSize: Tokens.fontCaption + 1
                color: Theme.on_surface_var
            }
            Rectangle {
                implicitWidth: mcBadge.implicitWidth + 14
                implicitHeight: 20
                radius: 10
                color: U.withAlpha(Theme.on_surface, Theme.isDark ? 0.07 : 0.06)
                C.AppText {
                    id: mcBadge
                    anchors.centerIn: parent
                    text: mc.badge
                    font.pixelSize: Tokens.fontMicro
                    weight: Tokens.weightDemiBold
                    color: Theme.on_surface_var
                }
            }
        }
    }

    // Five looks as a 2-column grid of little frames.
    component StyleGrid: GridLayout {
        columns: width >= 420 ? 3 : 2
        columnSpacing: Tokens.space2
        rowSpacing: Tokens.space2
        Repeater {
            model: VoiceStudio.styles
            StyleCard {
                required property var modelData
                Layout.fillWidth: true
                Layout.preferredWidth: 1
                spec: modelData
                selected: root.styleKey === modelData.key
                rtl: root._rtlPreview
                onPicked: root.styleKey = modelData.key
            }
        }
    }

    component SizePos: GridLayout {
        columns: 2
        columnSpacing: Tokens.space3
        rowSpacing: Tokens.space2
        FieldLabel { text: I18n.tr("Size") }
        FieldLabel { text: I18n.tr("Position") }
        C.SlideSegment {
            Layout.fillWidth: true
            equal: true; solid: false; cellHeight: 30
            fontSize: Tokens.fontCaption + 1
            model: [I18n.tr("Small"), I18n.tr("Medium"), I18n.tr("Large")]
            currentIndex: ["s", "m", "l"].indexOf(root.sizeKey)
            onActivated: (i) => root.sizeKey = ["s", "m", "l"][i]
        }
        C.SlideSegment {
            Layout.fillWidth: true
            equal: true; solid: false; cellHeight: 30
            fontSize: Tokens.fontCaption + 1
            model: [I18n.tr("Bottom"), I18n.tr("Middle"), I18n.tr("Top")]
            currentIndex: ["bottom", "middle", "top"].indexOf(root.posKey)
            onActivated: (i) => root.posKey = ["bottom", "middle", "top"][i]
        }
    }

    component ToggleRow: RowLayout {
        id: tgl
        property string text: ""
        property bool checked: false
        signal toggled(bool on)
        Layout.fillWidth: true
        spacing: Tokens.space3
        C.AppText {
            Layout.fillWidth: true
            wrapMode: Text.Wrap
            text: tgl.text
            font.pixelSize: Tokens.fontBody
            color: Theme.on_surface
        }
        C.AppSwitch {
            checked: tgl.checked
            onToggled: tgl.toggled(checked)
        }
    }

    component Banner: Rectangle {
        id: bn
        property color tint: Theme.warn
        property string glyph: "info"
        property string text: ""
        Layout.fillWidth: true
        implicitHeight: bnRow.implicitHeight + Tokens.space3 * 2
        radius: Tokens.radiusMd
        color: U.withAlpha(bn.tint, 0.10)
        border.width: 1
        border.color: U.withAlpha(bn.tint, 0.32)
        RowLayout {
            id: bnRow
            anchors.fill: parent
            anchors.margins: Tokens.space3
            spacing: Tokens.space2
            C.IconGlyph { name: bn.glyph; fill: true; size: 16; glyphColor: bn.tint; Layout.alignment: Qt.AlignTop }
            C.AppText {
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                text: bn.text
                font.pixelSize: Tokens.fontCaption + 1
                color: Theme.on_surface
            }
        }
    }

    // One caption look: a little video frame with the sample line drawn the
    // way the burn will draw it, the name under it.
    component StyleCard: Rectangle {
        id: sc
        property var spec: ({})
        property bool selected: false
        property bool rtl: false
        signal picked()
        implicitHeight: 104
        radius: Tokens.radiusMd
        color: scHover.hovered ? U.hoverTint(Theme.surface, Theme.isDark) : Theme.surface
        border.width: sc.selected ? 2 : 1
        border.color: sc.selected ? Theme.primary : (scHover.hovered ? U.withAlpha(Theme.primary, 0.35) : Theme.border)
        HoverHandler { id: scHover; cursorShape: Qt.PointingHandCursor }
        TapHandler { onTapped: sc.picked() }

        readonly property string _sample: sc.rtl ? "امروز یه چیز خفن می‌سازیم" : "Today we build something wild"
        readonly property var _words: sc._sample.split(" ")

        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 5
            spacing: 5
            // the frame
            Rectangle {
                Layout.fillWidth: true
                Layout.fillHeight: true
                radius: Tokens.radiusSm
                clip: true
                gradient: Gradient {
                    orientation: Gradient.Horizontal
                    GradientStop { position: 0.0; color: "#2B3A55" }
                    GradientStop { position: 0.55; color: "#5A4A6E" }
                    GradientStop { position: 1.0; color: "#8A6A55" }
                }
                Rectangle {   // a horizon, so it reads as footage
                    anchors.left: parent.left; anchors.right: parent.right
                    y: parent.height * 0.58
                    height: parent.height * 0.42
                    color: "#1A2230"
                    opacity: 0.55
                }
                Rectangle {
                    visible: (sc.spec.box || "") !== ""
                    anchors.centerIn: capText
                    width: capText.implicitWidth + 10
                    height: capText.implicitHeight + 4
                    radius: sc.spec.key === "boxed" ? 6 : 3
                    color: sc.spec.box || "transparent"
                }
                Text {
                    id: capText
                    anchors.horizontalCenter: parent.horizontalCenter
                    anchors.bottom: parent.bottom
                    anchors.bottomMargin: 6
                    width: Math.min(implicitWidth, parent.width - 10)
                    horizontalAlignment: Text.AlignHCenter
                    wrapMode: Text.Wrap
                    maximumLineCount: 2
                    textFormat: (sc.spec.pill || "") !== "" ? Text.RichText : Text.StyledText
                    font.family: sc.rtl ? "Vazirmatn" : Fonts.body
                    font.pixelSize: sc.spec.key === "punch" ? 16 : (sc.spec.key === "pop" || sc.spec.key === "spotlight") ? 13 : 10
                    font.weight: sc.spec.weight || 600
                    font.capitalization: (sc.spec.upper && !sc.rtl) ? Font.AllUppercase : Font.MixedCase
                    color: sc.spec.fg || "#FFFFFF"
                    style: (sc.spec.outline || "") !== "" ? Text.Outline : Text.Normal
                    styleColor: sc.spec.outline || "#000000"
                    text: {
                        var w0 = sc._words
                        if ((sc.spec.pill || "") !== "")
                            return w0.slice(0, 1).join(" ") + " <span style='background-color:" + sc.spec.pill
                                 + "; color:" + (sc.spec.hi || "#FFFFFF") + "'>&nbsp;" + w0[1] + "&nbsp;</span>"
                        if (sc.spec.single)
                            return "<font color='" + (sc.spec.hi || "#FFFFFF") + "'>" + w0[1] + "</font>"
                        if ((sc.spec.hi || "") === "")
                            return sc._sample
                        // Bold Pop: two words on screen, the spoken one lit
                        var w = sc._words
                        return w.slice(0, 1).join(" ") + " <font color='" + sc.spec.hi + "'>" + w[1] + "</font>"
                    }
                }
            }
            RowLayout {
                Layout.fillWidth: true
                spacing: 4
                C.AppText {
                    Layout.fillWidth: true
                    text: sc.spec.name || ""
                    elide: Text.ElideRight
                    weight: Tokens.weightBold
                    font.pixelSize: Tokens.fontCaption + 1
                    color: sc.selected ? Theme.primary : Theme.text_strong
                }
                C.IconGlyph {
                    visible: sc.selected
                    name: "check_circle"; fill: true; size: 14
                    glyphColor: Theme.primary
                }
            }
        }
        C.LazyTooltip { show: scHover.hovered && (sc.spec.desc || "") !== ""; text: sc.spec.desc || "" }
    }

    component JobCard: Rectangle {
        id: jc
        property var job: ({})
        readonly property bool _run: jc.job.state === "running"
        readonly property bool _done: jc.job.state === "done"
        readonly property bool _fail: jc.job.state === "failed"
        readonly property color _ok: "#22C55E"
        implicitHeight: jcCol.implicitHeight + Tokens.space3 * 2
        radius: Tokens.radiusLg
        color: jc._run ? U.withAlpha(Theme.primary, Theme.isDark ? 0.07 : 0.05) : Theme.surface_low
        border.width: 1
        border.color: jc._fail ? U.withAlpha(Theme.warn, 0.32) : (jc._done ? U.withAlpha(jc._ok, 0.45)
                     : U.withAlpha(Theme.primary, 0.35))
        ColumnLayout {
            id: jcCol
            anchors.left: parent.left; anchors.right: parent.right
            anchors.top: parent.top
            anchors.margins: Tokens.space3
            spacing: Tokens.space2
            RowLayout {
                Layout.fillWidth: true
                spacing: Tokens.space2
                C.SpinnerGlyph {
                    visible: jc._run
                    name: "autorenew"; spinning: visible
                }
                C.IconGlyph {
                    visible: !jc._run
                    name: jc._fail ? "error" : (jc.job.state === "cancelled" ? "cancel" : "check_circle")
                    fill: true; size: 16
                    glyphColor: jc._fail ? Theme.warn : (jc._done ? jc._ok : Theme.on_surface_var)
                }
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 0
                    C.AppText {
                        Layout.fillWidth: true
                        text: (jc.job.kind === "dub" ? (jc.job.preview ? I18n.tr("Dub preview") : I18n.tr("AI dub"))
                                                     : I18n.tr("Captions"))
                              + (jc.job.langName ? " · " + jc.job.langName : "")
                        weight: Tokens.weightBold
                        font.pixelSize: Tokens.fontBody
                        color: Theme.text_strong
                        elide: Text.ElideRight
                    }
                    C.AppText {
                        Layout.fillWidth: true
                        text: jc.job.title || ""
                        font.pixelSize: Tokens.fontCaption
                        color: Theme.on_surface_var
                        elide: Text.ElideRight
                    }
                }
                C.PrimaryButton {
                    visible: jc._done && (jc.job.video || "") !== ""
                    implicitHeight: 30
                    iconName: "play_arrow"
                    text: I18n.tr("Play")
                    onClicked: VoiceStudio.openPath(jc.job.video)
                }
                C.PrimaryButton {
                    visible: !jc._run && jc.job.canResume === true
                    variant: jc._fail ? "primary" : "ghost"
                    implicitHeight: 30
                    iconName: "restart_alt"
                    text: jc._fail ? I18n.tr("Resume") : I18n.tr("Fill missing lines")
                    onClicked: VoiceStudio.resume(jc.job.id)
                }
                C.IconButton {
                    visible: jc._run
                    implicitWidth: 30; implicitHeight: 30; glyphSize: 15
                    iconName: "stop_circle"; accent: Theme.error
                    tip: I18n.tr("Cancel")
                    onClicked: VoiceStudio.cancel(jc.job.id)
                }
                C.IconButton {
                    visible: jc._done
                    implicitWidth: 30; implicitHeight: 30; glyphSize: 15
                    iconName: "folder_open"
                    tip: I18n.tr("Show in folder")
                    onClicked: VoiceStudio.revealPath(jc.job.video || (jc.job.files && jc.job.files.length ? jc.job.files[0] : ""))
                }
                C.IconButton {
                    visible: !jc._run
                    implicitWidth: 30; implicitHeight: 30; glyphSize: 15
                    iconName: "close"
                    tip: I18n.tr("Dismiss")
                    onClicked: VoiceStudio.dismiss(jc.job.id)
                }
            }
            C.MeterBar {
                visible: jc._run
                Layout.fillWidth: true
                barHeight: 6
                value: jc.job.pct || 0
                fillColor: Theme.primary
            }
            RowLayout {
                Layout.fillWidth: true
                spacing: Tokens.space2
                C.AppText {
                    visible: !jc._fail
                    Layout.fillWidth: true
                    wrapMode: Text.Wrap
                    text: jc._run ? (jc.job.stage || "") : (jc.job.message || jc.job.stage || "")
                    font.pixelSize: Tokens.fontCaption + 1
                    color: Theme.on_surface_var
                }
                C.AppText {
                    visible: jc._run
                    mono: true
                    text: Math.round((jc.job.pct || 0) * 100) + "%" + ((jc.job.eta || "") !== "" ? " · " + jc.job.eta : "")
                    font.pixelSize: Tokens.fontCaption
                    color: Theme.on_surface_var
                }
            }
            // v1.4.9: a failure is three quiet lines, never the provider's
            // raw reply: what happened, which model / when it clears, and
            // the one thing to do (the raw text is in the crash log)
            Rectangle {
                visible: jc._fail
                Layout.fillWidth: true
                implicitHeight: errCol.implicitHeight + Tokens.space2 * 2
                radius: Tokens.radiusMd
                color: U.withAlpha(Theme.warn, Theme.isDark ? 0.07 : 0.06)
                border.width: 1
                border.color: U.withAlpha(Theme.warn, 0.22)
                ColumnLayout {
                    id: errCol
                    anchors.left: parent.left; anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    anchors.leftMargin: Tokens.space3; anchors.rightMargin: Tokens.space3
                    spacing: 2
                    C.AppText {
                        Layout.fillWidth: true
                        text: jc.job.errTitle || jc.job.message || I18n.tr("Something went wrong")
                        weight: Tokens.weightDemiBold
                        font.pixelSize: Tokens.fontCaption + 1
                        color: Theme.text_strong
                        elide: Text.ElideRight
                    }
                    C.AppText {
                        visible: (jc.job.errDetail || "") !== ""
                        Layout.fillWidth: true
                        text: jc.job.errDetail || ""
                        mono: true
                        font.pixelSize: Tokens.fontCaption
                        color: Theme.on_surface_var
                        elide: Text.ElideRight
                    }
                    C.AppText {
                        visible: (jc.job.errHint || "") !== ""
                        Layout.fillWidth: true
                        wrapMode: Text.Wrap
                        text: jc.job.errHint || ""
                        font.pixelSize: Tokens.fontCaption
                        color: Theme.on_surface_var
                        opacity: 0.85
                    }
                }
            }
        }
    }
}
