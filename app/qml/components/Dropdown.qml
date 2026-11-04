import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Templates as T
import QtQuick.Effects
import NicheScope
import "../Utils.js" as U

// Themed dropdown matching the legacy make_combo() chevron-pill look.
ComboBox {
    id: root
    implicitHeight: Tokens.fieldHeight
    font.family: Fonts.body
    font.pixelSize: Tokens.fontBody
    // Idle border colour — overridable so a section can request a stronger,
    // more visible border than the default hairline.
    property color borderColor: Theme.border
    // Soft bottom drop shadow — opt-in so it only shows where a section wants
    // its fields to read as raised cards (e.g. the Niche filter panel).
    property bool shadow: false
    // Transparent variant, matching LineField.transparentBg: no filled well,
    // just a 1px hairline, so a form of mixed field types stays consistent.
    property bool transparentBg: false
    // When the popup was last closed, in ms — the editable MouseArea's
    // just-closed guard (see below).
    property real _closedAt: 0
    // Optional per-row leading glyph names, index-aligned with `model`. When
    // set, each row (and the closed field) shows the matching icon — used by
    // the AI-provider picker to carry each brand's logo.
    property var icons: []
    // Tallest the open list may grow before it scrolls. Long catalogues
    // (59 languages, 77 countries) ask for more than the 280 default.
    property int popupMaxHeight: 280
    // Optional second line per row (index-aligned with `model`), e.g. what a
    // data source costs. Rows with a hint grow to fit it.
    property var hints: []
    // Minimum popup width. The list also grows on its own to fit the widest
    // row, so a long option is never clipped by a narrow field.
    property int popupWidth: 0
    property real _fitWidth: 0
    // Where the field sat when the list opened (window coords), see _moveGuard.
    property point _openPos: Qt.point(0, 0)
    function _measure() {
        var w = 0
        for (var i = 0; i < root.count; ++i) {
            rowMetrics.text = root.textAt(i)
            w = Math.max(w, rowMetrics.advanceWidth)
        }
        var lead = (root.icons && root.icons.length) ? 24 : 0
        root._fitWidth = Math.ceil(w + lead + Tokens.space3 + Tokens.space4 + Tokens.space1 * 2 + 8)
    }
    TextMetrics { id: rowMetrics; font.family: Fonts.body; font.pixelSize: Tokens.fontBody }

    // A Popup lives in the window overlay and does NOT follow its field when
    // an ancestor scrolls, so an open list used to stay pinned where it was
    // (over the title bar) while the page moved under it. Close it the moment
    // the field moves, the way native drop-downs do.
    Timer {
        id: _moveGuard
        interval: 60
        repeat: true
        running: root.popup.visible
        onTriggered: {
            var p = root.mapToItem(null, 0, 0)
            if (Math.abs(p.x - root._openPos.x) > 1 || Math.abs(p.y - root._openPos.y) > 1)
                root.popup.close()
        }
    }
    readonly property string _currentIcon:
        (icons && currentIndex >= 0 && currentIndex < icons.length)
            ? icons[currentIndex] : ""

    // Pointing-hand cursor on the field and on every row in the popup — a flat
    // pill gives no other hint that it opens.
    HoverHandler {
        enabled: root.enabled
        cursorShape: Qt.PointingHandCursor
    }

    delegate: ItemDelegate {
        id: rowDelegate
        readonly property string hint: (root.hints && index < root.hints.length)
                                       ? (root.hints[index] || "") : ""
        width: ListView.view ? ListView.view.width : root.width
        height: hint !== "" ? Math.max(Tokens.fieldHeight, rowCol.implicitHeight + Tokens.space2 * 2)
                            : Tokens.fieldHeight
        highlighted: root.highlightedIndex === index
        HoverHandler { cursorShape: Qt.PointingHandCursor }
        contentItem: Row {
            spacing: Tokens.space2
            leftPadding: Tokens.space3
            IconGlyph {
                id: rowGlyph
                visible: root.icons && index < root.icons.length && root.icons[index] !== ""
                name: (root.icons && index < root.icons.length) ? root.icons[index] : ""
                size: 16
                glyphColor: highlighted ? Theme.primary : Theme.on_surface_var
                anchors.verticalCenter: parent.verticalCenter
            }
            Column {
                id: rowCol
                anchors.verticalCenter: parent.verticalCenter
                width: rowDelegate.width - Tokens.space3 - Tokens.space3
                       - (rowGlyph.visible ? rowGlyph.width + Tokens.space2 : 0)
                spacing: 2
                AppText {
                    width: parent.width
                    text: modelData
                    elide: Text.ElideRight
                    color: highlighted ? Theme.primary : Theme.on_surface
                    weight: rowDelegate.hint !== "" ? Tokens.weightDemiBold : Tokens.weightRegular
                }
                AppText {
                    visible: rowDelegate.hint !== ""
                    width: parent.width
                    text: rowDelegate.hint
                    wrapMode: Text.WordWrap
                    color: Theme.on_surface_var
                    font.pixelSize: Tokens.fontCaption
                    lineHeight: 1.15
                }
            }
        }
        // Inset, rounded highlight so the selected row reads as a pill inside
        // the popup rather than a full-bleed bar touching the edges.
        background: Rectangle {
            anchors.fill: parent
            anchors.leftMargin: Tokens.space1
            anchors.rightMargin: Tokens.space1
            anchors.topMargin: 2
            anchors.bottomMargin: 2
            radius: Tokens.radiusSm
            color: U.withAlpha(Theme.primary, highlighted ? 0.14 : 0.0)
            Behavior on color { enabled: !Tokens.themeSwapping; ColorAnimation { duration: Tokens.durFast } }
            // accent tick on the row that is the current value
            Rectangle {
                visible: index === root.currentIndex
                width: 3; height: parent.height * 0.5
                radius: 2
                color: Theme.primary
                anchors.left: parent.left
                anchors.leftMargin: 3
                anchors.verticalCenter: parent.verticalCenter
            }
        }
    }

    // The chevron, inside a real hit target rather than as a bare glyph.
    //
    // A Text is only as wide as its glyph (~18px), so "click the arrow" was a
    // pixel-hunt. This is a full-height strip at the trailing edge, so the arrow
    // is hit whenever the pointer is anywhere near it. It carries no handler of
    // its own — the whole-control MouseArea below owns the tap, so the chevron
    // and the field can never disagree about whether the popup should be open.
    indicator: Item {
        implicitWidth: 38
        implicitHeight: root.height
        x: root.mirrored ? 0 : root.width - width
        y: 0
        IconGlyph {
            anchors.centerIn: parent
            name: "expand_more"
            size: 18
            glyphColor: root.popup.visible ? Theme.primary : Theme.on_surface_var
            rotation: root.popup.visible ? 180 : 0
            Behavior on rotation { NumberAnimation { duration: Tokens.durMed; easing.type: Easing.OutCubic } }
        }
    }

    // The content is a real TextField, not a Text: an editable ComboBox types
    // into its contentItem, and a plain Text silently swallows every keystroke.
    // When `editable` is false the field is read-only, so it still behaves
    // exactly like the old label — it just no longer blocks the editable case
    // (the AI model picker needs one: a router wants an exact id like
    // "openai/gpt-4o-mini" that no fetched list is guaranteed to carry).
    contentItem: TextField {
        id: editInput
        // v3.40.1 — the premium right-click menu, only where the box is
        // really a text box (an editable picker); a read-only dropdown is a
        // label and gets no edit menu at all.
        T.ContextMenu.menu: root.editable ? editMenu : null
        EditMenu { id: editMenu; editor: editInput }
        text: root.editable ? root.editText : root.displayText
        readOnly: !root.editable
        // Read-only means "not a text box", so it shouldn't take focus by click
        // or tab — that would put a caret in what looks like a label.
        activeFocusOnPress: root.editable
        focusPolicy: root.editable ? Qt.StrongFocus : Qt.NoFocus
        selectByMouse: root.editable
        inputMethodHints: root.inputMethodHints
        validator: root.validator
        color: Theme.on_surface
        placeholderTextColor: Theme.outline
        font.family: Fonts.body
        font.pixelSize: Tokens.fontBody
        verticalAlignment: Text.AlignVCenter
        // Leave room for the leading brand glyph when the closed field shows one.
        leftPadding: root._currentIcon !== "" ? Tokens.space4 + 22 : Tokens.space4
        rightPadding: root.indicator.width + Tokens.space2
        // The ComboBox's own background draws the frame.
        background: null
        // A read-only TextField keeps its caret at the END, so a long value
        // ("Official API · exact, 100 searches/day") scrolled out of view from
        // the LEFT and showed "ficial API…". Pin the view to the start.
        onTextChanged: if (readOnly) cursorPosition = 0
        onWidthChanged: if (readOnly) cursorPosition = 0
        Component.onCompleted: cursorPosition = 0
    }

    // ONE tap target for the whole control, chevron included — for BOTH modes.
    //
    // A read-only TextField CONSUMES mouse presses through its internal mouse
    // area, so a plain TapHandler on the root never sees the tap (passive grabs
    // don't survive the TextField accepting the press). That is the whole bug
    // the editable model picker had: ComboBox's built-in open-on-click only
    // fires for a press on the INDICATOR, so the list opened only when the
    // arrow was hit exactly. This MouseArea sits ON TOP of everything (high z)
    // and handles the press itself, so clicking anywhere on the field works in
    // both modes.
    //
    // Read-only: toggles.
    // Editable: open-only, and the caret is moved to the field so typing works
    // right away. The just-closed guard matters: CloseOnPressOutside CLOSES the
    // popup during the same press that reaches this MouseArea, so without the
    // guard a click meant to dismiss the list would immediately re-open it.
    MouseArea {
        anchors.fill: parent
        z: 10
        cursorShape: Qt.PointingHandCursor
        onPressed: (m) => {
            if (!root.editable) return
            root.contentItem.forceActiveFocus()
            if (Date.now() - root._closedAt > 250 && !root.popup.visible)
                root.popup.open()
        }
        onClicked: if (!root.editable)
            root.popup.visible ? root.popup.close() : root.popup.open()
    }

    // Leading glyph on the closed field (brand logo for the provider picker).
    IconGlyph {
        visible: root._currentIcon !== ""
        name: root._currentIcon
        size: 16
        glyphColor: Theme.on_surface_var
        anchors.left: parent.left
        anchors.leftMargin: Tokens.space4
        anchors.verticalCenter: parent.verticalCenter
    }

    background: Rectangle {
        id: bg
        radius: Tokens.radiusMd
        // Same three-state treatment as LineField's transparent style: nothing
        // at rest, a whisper of fill on hover (a border alone gives the pointer
        // no feedback), and a recoloured 1px ring on focus.
        color: root.transparentBg
               ? (bgHover.hovered && !root.activeFocus
                  ? U.withAlpha(Theme.on_surface, 0.04) : U.withAlpha(Theme.on_surface, 0))
               : Theme.input_bg
        // Hairline at every state — see LineField: a width change on focus shifts
        // the field's content by a pixel and reads as a twitch.
        border.width: 1
        border.color: (root.activeFocus || root.popup.visible)
                      ? Theme.primary
                      : bgHover.hovered && !root.transparentBg
                      ? U.withAlpha(Theme.primary, 0.40)
                      : (root.transparentBg
                         // 0.32 clears the 3:1 contrast floor a component
                         // boundary needs; the first pass used 0.20 and vanished
                         // on a bright screen.
                         ? U.withAlpha(Theme.on_surface,
                                       bgHover.hovered ? 0.45 : 0.32)
                         : root.borderColor)
        Behavior on border.color { enabled: !Tokens.themeSwapping; ColorAnimation { duration: Tokens.durFast } }
        Behavior on color { enabled: !Tokens.themeSwapping; ColorAnimation { duration: Tokens.durFast } }
        HoverHandler { id: bgHover }

        // v4 focus / open halo — same 3px outer ring as LineField.
        Rectangle {
            visible: !root.shadow
            anchors.fill: parent
            anchors.margins: -3
            radius: parent.radius + 3
            color: "transparent"
            border.width: 3
            border.color: U.withAlpha(Theme.primary, Theme.isDark ? 0.24 : 0.18)
            opacity: (root.popup.visible || (root.editable && root.activeFocus)) ? 1 : 0
            Behavior on opacity { NumberAnimation { duration: Tokens.durMed; easing.type: Easing.OutCubic } }
        }

        // Soft downward shadow, opt-in via `shadow`. Only enabled at a sane
        // size so a very large field never allocates an oversized GPU layer.
        layer.enabled: root.shadow && height > 0 && height <= 4096
        layer.effect: MultiEffect {
            shadowEnabled: true
            shadowColor: U.withAlpha(Qt.rgba(0, 0, 0, 1), Theme.isDark ? 0.40 : 0.12)
            shadowVerticalOffset: 4
            shadowBlur: 0.5
            shadowScale: 1.0
        }
    }

    popup: Popup {
        y: root.height + 4
        // never narrower than the field; wide enough for the longest row
        width: Math.max(root.width, root.popupWidth,
                        root.hints && root.hints.length ? 0 : root._fitWidth)
        // keep the sheet inside the window (it used to run off the edge or
        // up over the title bar)
        margins: Tokens.space2
        implicitHeight: Math.min(contentItem.implicitHeight, root.popupMaxHeight)
        padding: Tokens.space1
        onAboutToShow: {
            root._measure()
            root._openPos = root.mapToItem(null, 0, 0)
        }
        onClosed: root._closedAt = Date.now()
        // Unfold downward from the field's top edge.
        transformOrigin: Item.Top
        enter: Transition {
            NumberAnimation { property: "opacity"; from: 0; to: 1; duration: Tokens.durMed; easing.type: Easing.OutCubic }
            NumberAnimation { property: "scale"; from: 0.94; to: 1; duration: Tokens.durSlow; easing.type: Easing.OutBack; easing.overshoot: 1.3 }
            NumberAnimation { property: "y"; from: root.height - 6; to: root.height + 4; duration: Tokens.durSlow; easing.type: Easing.OutCubic }
        }
        exit: Transition {
            NumberAnimation { property: "opacity"; from: 1; to: 0; duration: Tokens.durFast }
            NumberAnimation { property: "scale"; from: 1; to: 0.97; duration: Tokens.durFast; easing.type: Easing.OutCubic }
        }
        background: Rectangle {
            id: popBg
            radius: Tokens.radiusMd
            color: Theme.dropdown_bg
            border.width: 1
            border.color: U.mix(Theme.border, Theme.primary, 0.18)
            // floating-sheet shadow; only the frame is layered, the rows are
            // the popup's contentItem and stay crisp.
            layer.enabled: popBg.height > 0 && popBg.height <= 4096
            layer.effect: MultiEffect {
                shadowEnabled: true
                shadowColor: U.withAlpha(Qt.rgba(0, 0, 0, 1), Theme.isDark ? 0.55 : 0.14)
                shadowVerticalOffset: 10
                shadowBlur: 0.8
                shadowScale: 1.0
            }
        }
        contentItem: ListView {
            clip: true
            implicitHeight: contentHeight
            model: root.popup.visible ? root.delegateModel : null
            currentIndex: root.highlightedIndex
            ScrollIndicator.vertical: ScrollIndicator {}
        }
    }
}
