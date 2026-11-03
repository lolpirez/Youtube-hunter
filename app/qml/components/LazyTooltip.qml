import QtQuick
import NicheScope

// LazyTooltip (v1.0.9 perf) — an AppTooltip that is only BUILT the first time
// it is asked to show, then kept (so its fade-out still plays).
//
// AppTooltip is a full item tree (bubble, shadow rings, caret, timers, window-
// bound geometry) and it moves itself into the overlay on creation. Declared
// directly, every IconButton / card hint paid for one up front: a grid page of
// 24 video cards carried ~260 hidden tooltips, built with the page and re-laid
// out on every window resize. Most of them are never hovered.
//
//   LazyTooltip { show: area.containsMouse; text: "…"; anchorItem: area }
//
// `anchorItem` defaults to the parent, i.e. exactly what a directly declared
// AppTooltip anchored to.
Loader {
    id: lt
    property bool show: false
    property string text: ""
    property int delay: 400
    property bool preferAbove: false
    property string boundsName: ""
    property Item anchorItem: parent

    active: false
    onShowChanged: if (show && text !== "") active = true
    Component.onCompleted: if (show && text !== "") active = true

    sourceComponent: AppTooltip {
        anchor: lt.anchorItem
        visible: lt.show && lt.text !== ""
        text: lt.text
        delay: lt.delay
        preferAbove: lt.preferAbove
        boundsName: lt.boundsName
    }
}
