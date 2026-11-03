import QtQuick
import QtQuick.Controls.Basic
import NicheScope
import "../Utils.js" as U

// A ScrollView with the app's standard eased wheel scrolling baked in.
//
// The default per-notch momentum flick reads as a jerky jump on tall content,
// so the wheel is intercepted and contentY is animated toward a clamped target
// that accumulates across notches (OutCubic over durMed). Lifted from the
// hand-rolled copies the Search Archive / Niche Finder / Downloads pages grew
// so every page scrolls identically instead of each reinventing it.
//
// WheelHandler and NumberAnimation are not Items, so they don't disturb
// contentChildren — the page's own content Item stays the single content child.
ScrollView {
    id: root
    contentWidth: availableWidth
    clip: true

    property real _wheelTarget: 0

    // Always-visible scrollbar. The stock AsNeeded bar spends most of its life
    // faded to zero opacity, so the only way to find it is to hover the edge
    // blindly. Here the slim track is always on screen while the page can
    // actually scroll, and the handle brightens on hover / drag.
    ScrollBar.vertical: ScrollBar {
        id: vbar
        readonly property bool scrollable: root.contentItem
                                           && root.contentItem.contentHeight > root.contentItem.height + 1
        policy: scrollable ? ScrollBar.AlwaysOn : ScrollBar.AlwaysOff
        // Same placement the Basic style gives its own bar: without these the
        // override sits unpositioned (and the style's bar is the one you see).
        parent: root
        x: root.mirrored ? 0 : root.width - width
        y: root.topPadding
        height: root.availableHeight
        active: root.ScrollBar.horizontal.active
        implicitWidth: 12
        padding: 3
        // Keep the handle grabbable even on very long pages (the default ratio
        // can shrink it to a few pixels).
        minimumSize: Math.min(0.5, 28 / Math.max(1, height))

        contentItem: Rectangle {
            implicitWidth: 6
            radius: width / 2
            color: vbar.pressed ? Theme.primary
                 : vbar.hovered ? U.withAlpha(Theme.primary, 0.90)
                 : U.withAlpha(Theme.on_surface, Theme.isDark ? 0.30 : 0.24)
            Behavior on color { enabled: !Tokens.themeSwapping; ColorAnimation { duration: Tokens.durFast } }
        }
        background: Rectangle {
            radius: 5
            color: U.withAlpha(Theme.on_surface, Theme.isDark ? 0.055 : 0.045)
            border.width: 1
            border.color: U.withAlpha(Theme.outline_variant, 0.55)
        }
    }

    // Wheel tuning. A page can override these; the defaults are the behaviour
    // every page already had.
    //   wheelStep     — px travelled per mouse-wheel NOTCH (angleDelta 120)
    //   wheelDuration — the glide for a single notch, in ms
    property real wheelStep: 120
    property int wheelDuration: Tokens.durMed

    // The trackpad path below moves contentY directly (no animation to
    // observe), so it keeps its own short-lived flag: `gliding` must stay true
    // for as long as the fingers are still feeding it deltas.
    property bool _pixelMoving: false
    Timer {
        id: pixelIdle
        interval: 120
        repeat: false
        onTriggered: root._pixelMoving = false
    }

    // True while the content is really moving: a wheel glide, a page jump, or
    // a drag / flick. Grids of rich cards bind this to their delegates so the
    // hover effects stand down while scrolling — see ArchiveVideoCard's
    // `scrollIdle`. Without it every card that slid under a resting pointer
    // started a 720 ms cover zoom, and that zoom re-renders the card's layered
    // thumbnail + mask on EVERY frame, which is what made long grids feel
    // stiff instead of smooth.
    //
    // v1.0.9: `gliding` turns on at once but turns OFF 140 ms after the content
    // really stops. Every grid card binds it (scrollIdle), so each on/off flip
    // re-ran ~25 hover bindings on all 24 cards. A fast wheel spin used to flip
    // it between notches, and the hover state of the card under the pointer
    // popped in the instant a glide ended; now one spin is one long "gliding".
    //
    // v1.0.11 "hover comes back at once":
    //   * `moving` is no longer part of it. Flickable keeps `moving` up through
    //     its own wheel momentum, rebound and movement-ending timers, which
    //     could hold `gliding` (and so every card's hover) off for seconds
    //     after the content had visibly stopped. dragging / flicking cover a
    //     real drag or fling.
    //   * a touchpad / free-spin / "smooth" wheel keeps sending pixel deltas
    //     long after the user let go (OS inertia, up to ~10 s on a free-spin
    //     wheel), and kept doing so with the page pinned at its top or bottom.
    //     Only deltas that really move the content count now.
    //   * the POINTER wins: `gliding` exists so cards sliding under a resting
    //     pointer do not light up. Once the wheel has been quiet for
    //     _pointerQuietMs and the pointer itself moves more than a few px,
    //     the user is aiming at a card: hover is released at once, even if a
    //     glide tail or inertia is still settling. The next wheel event that
    //     moves the content arms it again.
    readonly property bool _movingNow: wheelFollow.running || toTopAnim.running
                                       || _pixelMoving
                                       || (contentItem !== null && contentItem !== undefined
                                           && (contentItem.dragging === true
                                               || contentItem.flicking === true))
    property bool gliding: false
    property bool _pointerWins: false
    property point _glideAnchor: Qt.point(0, 0)
    readonly property int _pointerQuietMs: 150
    readonly property real _pointerSlopPx: 6

    function _armGlide() {
        root._pointerWins = false
        glideSettle.stop()
        if (!root.gliding) {
            root._glideAnchor = pointerProbe.point.scenePosition
            root.gliding = true
        }
    }
    on_MovingNowChanged: {
        if (_movingNow) { if (!root._pointerWins) root._armGlide() }
        else glideSettle.restart()
    }
    Timer {
        id: glideSettle
        interval: 140
        repeat: false
        onTriggered: {
            root.gliding = false
            root._pointerWins = false
        }
    }

    // Passive probe: never blocks the cards' own hover (HoverHandler is not
    // blocking by default). Content moving under a still pointer does not
    // change scenePosition, so only real pointer motion gets through.
    HoverHandler {
        id: pointerProbe
        onPointChanged: {
            if (!root.gliding || root._pointerWins) return
            const p = pointerProbe.point.scenePosition
            // still wheeling: a hand on the wheel jiggles the mouse. Follow
            // it, so only motion AFTER the wheel went quiet can release.
            if (Date.now() - root._wheelLastMs < root._pointerQuietMs) {
                root._glideAnchor = p
                return
            }
            const dx = p.x - root._glideAnchor.x
            const dy = p.y - root._glideAnchor.y
            if (dx * dx + dy * dy < root._pointerSlopPx * root._pointerSlopPx) return
            root._pointerWins = true
            glideSettle.stop()
            root.gliding = false
        }
    }

    // Jump back to the top, eased. Called when the content underneath is
    // REPLACED rather than extended — turning a grid page, switching an archive
    // section — where leaving the viewport at the old scroll offset drops you
    // into the middle (or the end) of a page you haven't seen the start of.
    function toTop() {
        const fl = root.contentItem
        if (!fl) return
        wheelFollow.stop()
        root._wheelTarget = 0
        if (fl.contentY <= 0) return
        root._armGlide()
        toTopAnim.stop()
        toTopAnim.from = fl.contentY
        toTopAnim.to = 0
        toTopAnim.start()
    }
    NumberAnimation {
        id: toTopAnim
        target: root.contentItem
        property: "contentY"
        duration: Tokens.durSlow
        easing.type: Easing.OutCubic
    }

    // Scroll so `item`'s top edge sits at the top of the viewport, eased. Used
    // where the thing being paged is a grid PARTWAY down a long page (the AI
    // Ideas reference grid): toTop() would throw the reader back to the page
    // heading, while leaving the offset alone drops them into the middle of a
    // page of cards they haven't seen the start of. `margin` is breathing room
    // above the item.
    function scrollToItem(item, margin) {
        const fl = root.contentItem
        if (!fl || !item) return
        const m = margin === undefined ? Tokens.space4 : margin
        // contentItem IS the flickable that holds the page's content Item, so
        // mapping into it gives a position already in content coordinates.
        const pos = item.mapToItem(fl.contentItem ? fl.contentItem : fl, 0, 0)
        const max = Math.max(0, fl.contentHeight - fl.height)
        const target = Math.max(0, Math.min(max, pos.y - m))
        wheelFollow.stop()
        root._wheelTarget = target
        if (Math.abs(fl.contentY - target) < 1) return
        root._armGlide()
        toTopAnim.stop()
        toTopAnim.from = fl.contentY
        toTopAnim.to = target
        toTopAnim.start()
    }

    WheelHandler {
        target: null
        acceptedDevices: PointerDevice.Mouse | PointerDevice.TouchPad
        onWheel: (ev) => {
            // Let horizontal / trackpad-only pans reach nested content.
            if (ev.angleDelta.y === 0 && ev.pixelDelta.y === 0) return
            const fl = root.contentItem
            if (!fl) return
            const max = Math.max(0, fl.contentHeight - fl.height)

            // ── v3.52: honour the device instead of flattening every event ──
            // A trackpad (and a "smooth scrolling" mouse) sends many small
            // pixelDelta events that ALREADY carry the user's momentum. The old
            // handler turned each one into a fixed 120 px animated jump, so one
            // flick became a stack of rubbery 190 ms hops that kept restarting
            // before they finished — the "stiff / not quite laggy" scroll on
            // long grids. Pixel deltas are now applied directly: no second
            // animation on top of the one the OS already made.
            if (ev.pixelDelta.y !== 0) {
                wheelFollow.stop()
                root._wheelBoost = 1
                const y = Math.max(0, Math.min(max, fl.contentY - ev.pixelDelta.y))
                root._wheelTarget = y
                // v1.0.11: an inertia tail pressing against the top / bottom
                // moves nothing, so it must not hold the cards' hover off
                if (Math.abs(y - fl.contentY) < 0.5) return
                fl.cancelFlick()
                fl.contentY = y
                root._wheelLastMs = Date.now()
                root._pixelMoving = true
                pixelIdle.restart()
                root._armGlide()
                return
            }
            // A real wheel: scale by the number of notches actually reported
            // (angleDelta is a multiple of 120 — 240 on some mice is TWO).
            //
            // v1.0.9 — continuous glide instead of one animation per notch.
            // Each notch used to restart an OutCubic NumberAnimation from the
            // current spot; OutCubic starts at 3x its average speed, so a fast
            // spin surged and braked on every notch ("fast but not smooth").
            // Now notches only move a TARGET and a per-frame follower eases
            // contentY toward it (exponential approach, frame-time based, so
            // it is the same on 60 / 144 Hz). Speed stays continuous across
            // notches, a quick spin accelerates the way a browser does, and a
            // reversal turns around at once.
            if (ev.angleDelta.y === 0) return
            const notches = ev.angleDelta.y / 120
            const now = Date.now()
            const gap = now - root._wheelLastMs
            const sameDir = (notches > 0) === (root._wheelLastDir > 0)
            root._wheelLastMs = now
            root._wheelLastDir = notches
            if (!sameDir || gap > root.wheelBoostWindow) root._wheelBoost = 1
            else root._wheelBoost = Math.min(root.wheelBoostMax, root._wheelBoost + root.wheelBoostStep)
            const base = (wheelFollow.running && sameDir) ? root._wheelTarget : fl.contentY
            // rounded: a glide that comes to rest between two pixels leaves
            // every native-rendered label slightly soft
            const target = Math.round(Math.max(0, Math.min(max, base - notches * root.wheelStep * root._wheelBoost)))
            if (Math.abs(target - fl.contentY) < 0.5) return
            fl.cancelFlick()
            root._wheelTarget = target
            root._armGlide()
            if (!wheelFollow.running) wheelFollow.start()
        }
    }

    // ── wheel glide engine (v1.0.9) ──
    // tau = the follower's time constant: ~95 % of a notch is covered in 3 tau.
    // Derived from wheelDuration so a page that tuned its glide keeps the feel.
    property real wheelTau: Math.max(0.045, root.wheelDuration / 3000)
    // A notch that lands within wheelBoostWindow ms of the previous one (same
    // direction) travels further, up to wheelBoostMax x wheelStep.
    property int wheelBoostWindow: 90
    property real wheelBoostStep: 0.18
    property real wheelBoostMax: 2.6
    property real _wheelBoost: 1
    property real _wheelLastMs: 0
    property real _wheelLastDir: 0
    FrameAnimation {
        id: wheelFollow
        onTriggered: {
            const fl = root.contentItem
            // a drag, a flick or the scrollbar handle owns contentY now
            if (!fl || fl.dragging || fl.flicking || vbar.pressed) { wheelFollow.stop(); return }
            const max = Math.max(0, fl.contentHeight - fl.height)
            const target = Math.max(0, Math.min(max, root._wheelTarget))
            root._wheelTarget = target
            const y = fl.contentY
            const d = target - y
            // v1.0.11: snap the last pixel. The exponential tail spent its
            // final ~50 ms crawling through sub-pixel steps nobody can see,
            // and `gliding` (no hover) lasted that much longer.
            if (Math.abs(d) < 1.0) { fl.contentY = target; wheelFollow.stop(); return }
            const dt = Math.min(0.05, Math.max(0.001, wheelFollow.frameTime))
            fl.contentY = y + d * (1 - Math.exp(-dt / root.wheelTau))
        }
    }

}
