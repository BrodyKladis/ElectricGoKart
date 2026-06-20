import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Window

Window {
    id: root
    width: 1200
    height: 675
    visible: true
    visibility: Window.FullScreen
    color: "#0b0f14"
    title: "Dash V2"

    function rgba(r, g, b, a) {
        return Qt.rgba(r / 255, g / 255, b / 255, a)
    }

    function lerp(a, b, t) {
        return a + (b - a) * t
    }

    function blend(c1, c2, t) {
        return Qt.rgba(
            lerp(c1.r, c2.r, t),
            lerp(c1.g, c2.g, t),
            lerp(c1.b, c2.b, t),
            lerp(c1.a, c2.a, t)
        )
    }

    component SectionCard: Rectangle {
        radius: 22
        color: "#131a22"
        border.color: "#233041"
        border.width: 1
    }

    component ProgressBarCard: Item {
        property string label: ""
        property real value: 0
        property color fillColor: "lime"
        implicitHeight: 80
        Column {
            anchors.fill: parent
            spacing: 8
            Text {
                text: label + ": " + Math.round(value * 100) + "%"
                color: "#dbe7f3"
                font.pixelSize: 22
                font.weight: Font.DemiBold
            }
            Rectangle {
                width: parent.width
                height: 28
                radius: 12
                color: "#0d131a"
                border.color: "#253242"
                Rectangle {
                    width: Math.max(6, parent.width * value)
                    height: parent.height
                    radius: parent.radius
                    color: fillColor
                }
            }
        }
    }

    component CircularGauge: Item {
        id: gauge
        property string label: "Gauge"
        property string unit: ""
        property real value: 0
        property real maxValue: 100
        property color needleColor: "red"
        width: 260
        height: 290

        function angleFromValue(v, maxV) {
            var n = Math.max(0, Math.min(1, v / maxV))
            return -135 + 270 * n
        }

        function pointX(cx, r, deg) {
            return cx + r * Math.cos((deg - 90) * Math.PI / 180)
        }

        function pointY(cy, r, deg) {
            return cy + r * Math.sin((deg - 90) * Math.PI / 180)
        }

        Column {
            anchors.fill: parent
            spacing: 8
            Canvas {
                id: canvas
                width: 260
                height: 230
                onPaint: {
                    var ctx = getContext("2d")
                    ctx.reset()

                    var cx = width / 2
                    var cy = 120
                    var r = 92

                    ctx.lineWidth = 16
                    ctx.strokeStyle = "#18222d"
                    ctx.beginPath()
                    ctx.arc(cx, cy, r, Math.PI * (225 / 180), Math.PI * (495 / 180), false)
                    ctx.stroke()

                    ctx.lineWidth = 16
                    ctx.strokeStyle = "#2a9d8f"
                    ctx.beginPath()
                    ctx.arc(cx, cy, r, Math.PI * (225 / 180), Math.PI * (225 + 270 * Math.max(0, Math.min(1, value / maxValue)) / 180), false)
                    ctx.stroke()

                    ctx.lineWidth = 3
                    ctx.strokeStyle = "#dce8f5"
                    for (var i = 0; i <= 16; i++) {
                        var a = -135 + (270 * i / 16)
                        ctx.beginPath()
                        ctx.moveTo(pointX(cx, r - 14, a), pointY(cy, r - 14, a))
                        ctx.lineTo(pointX(cx, r + 2, a), pointY(cy, r + 2, a))
                        ctx.stroke()
                    }

                    var needleAngle = angleFromValue(value, maxValue)
                    ctx.lineWidth = 5
                    ctx.strokeStyle = needleColor
                    ctx.beginPath()
                    ctx.moveTo(cx, cy)
                    ctx.lineTo(pointX(cx, r - 18, needleAngle), pointY(cy, r - 18, needleAngle))
                    ctx.stroke()

                    ctx.fillStyle = "#ffffff"
                    ctx.beginPath()
                    ctx.arc(cx, cy, 8, 0, Math.PI * 2)
                    ctx.fill()
                }
            }
            Text {
                anchors.horizontalCenter: parent.horizontalCenter
                text: label
                color: "#8fa4b8"
                font.pixelSize: 22
            }
            Text {
                anchors.horizontalCenter: parent.horizontalCenter
                text: value.toFixed(1) + " " + unit
                color: "#f6fbff"
                font.pixelSize: 28
                font.weight: Font.Bold
            }
        }
    }

    component SteeringDial: Item {
        id: dial
        property real commandAngle: 0
        property real wheelAngle: 0
        property real auraLevel: 0
        width: 420
        height: 420

        Canvas {
            id: steerCanvas
            anchors.fill: parent
            onPaint: {
                var ctx = getContext("2d")
                ctx.reset()

                var cx = width / 2
                var cy = height / 2
                var r = 140

                function drawRing(rr, lw, color) {
                    ctx.beginPath()
                    ctx.lineWidth = lw
                    ctx.strokeStyle = color
                    ctx.arc(cx, cy, rr, 0, Math.PI * 2)
                    ctx.stroke()
                }

                var white = rgba(255,255,255,1)
                var green = rgba(0,255,90,1)
                drawRing(r + 16, 10, blend(white, green, auraLevel * 0.35))
                drawRing(r + 28, 14, blend(white, green, auraLevel * 0.55))
                drawRing(r + 42, 18, blend(white, green, auraLevel * 0.8))

                ctx.beginPath()
                ctx.lineWidth = 4
                ctx.strokeStyle = "#e8eff7"
                ctx.arc(cx, cy, r, 0, Math.PI * 2)
                ctx.stroke()

                function lineToAngle(deg, color, lw) {
                    var rad = (deg - 90) * Math.PI / 180
                    ctx.beginPath()
                    ctx.lineWidth = lw
                    ctx.strokeStyle = color
                    ctx.moveTo(cx, cy)
                    ctx.lineTo(cx + r * Math.cos(rad), cy + r * Math.sin(rad))
                    ctx.stroke()
                }

                lineToAngle(wheelAngle, "#4da3ff", 4)
                lineToAngle(commandAngle, "#ff5f57", 4)

                function tick(deg) {
                    var rad = (deg - 90) * Math.PI / 180
                    ctx.beginPath()
                    ctx.lineWidth = 4
                    ctx.strokeStyle = "#ffffff"
                    ctx.moveTo(cx + (r - 14) * Math.cos(rad), cy + (r - 14) * Math.sin(rad))
                    ctx.lineTo(cx + r * Math.cos(rad), cy + r * Math.sin(rad))
                    ctx.stroke()
                }
                tick(-135)
                tick(135)
            }
        }
    }

    Keys.onEscapePressed: root.visibility = Window.Windowed
    Keys.onPressed: function(event) {
        if (event.key === Qt.Key_F11) {
            root.visibility = root.visibility === Window.FullScreen ? Window.Windowed : Window.FullScreen
            event.accepted = true
        } else if (event.modifiers & Qt.ControlModifier && event.key === Qt.Key_Q) {
            Qt.quit()
            event.accepted = true
        } else if (event.key === Qt.Key_Left) {
            backend.mockLeft()
            event.accepted = true
        } else if (event.key === Qt.Key_Right) {
            backend.mockRight()
            event.accepted = true
        } else if (event.key === Qt.Key_Up) {
            backend.mockAccelUp()
            event.accepted = true
        } else if (event.key === Qt.Key_Down) {
            backend.mockAccelDown()
            event.accepted = true
        } else if (event.key === Qt.Key_R) {
            backend.mockRegenUp()
            event.accepted = true
        } else if (event.key === Qt.Key_F) {
            backend.mockRegenDown()
            event.accepted = true
        } else if (event.key === Qt.Key_Space) {
            backend.togglePlayback()
            event.accepted = true
        }
    }

    Rectangle {
        anchors.fill: parent
        gradient: Gradient {
            GradientStop { position: 0.0; color: "#0b0f14" }
            GradientStop { position: 1.0; color: "#111a24" }
        }
    }

    RowLayout {
        anchors.fill: parent
        anchors.margins: 18
        spacing: 18

        SectionCard {
            Layout.fillHeight: true
            Layout.preferredWidth: parent.width * 0.33

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 20
                spacing: 14

                Text {
                    text: "Steering Dynamics"
                    color: "#f2f7fb"
                    font.pixelSize: 30
                    font.weight: Font.Bold
                }

                SteeringDial {
                    Layout.alignment: Qt.AlignHCenter
                    commandAngle: backend.steerAngle
                    wheelAngle: backend.wheelAngle
                    auraLevel: backend.auraLevel
                }

                Text {
                    text: "Powertrain Properties"
                    color: "#f2f7fb"
                    font.pixelSize: 24
                    font.weight: Font.DemiBold
                }

                ProgressBarCard {
                    Layout.fillWidth: true
                    label: "Acceleration"
                    value: backend.accelPercent
                    fillColor: "#38d46a"
                }

                ProgressBarCard {
                    Layout.fillWidth: true
                    label: "Regen"
                    value: backend.regenPercent
                    fillColor: "#ff9f43"
                }
            }
        }

        ColumnLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 18

            SectionCard {
                Layout.fillWidth: true
                Layout.preferredHeight: parent.height * 0.52

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 20
                    spacing: 8

                    Text {
                        text: "Gauge Cluster"
                        color: "#f2f7fb"
                        font.pixelSize: 30
                        font.weight: Font.Bold
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        Layout.topMargin: 10
                        spacing: 12

                        CircularGauge {
                            Layout.alignment: Qt.AlignHCenter
                            label: "Speed"
                            unit: "mph"
                            value: backend.speed
                            maxValue: 45
                            needleColor: "#ff5f57"
                        }

                        CircularGauge {
                            Layout.alignment: Qt.AlignHCenter
                            label: "Voltage"
                            unit: "V"
                            value: backend.voltage
                            maxValue: 75
                            needleColor: "#4da3ff"
                        }

                        ColumnLayout {
                            Layout.fillWidth: true
                            Layout.alignment: Qt.AlignVCenter
                            spacing: 10
                            Text {
                                text: "Current Draw"
                                color: "#8fa4b8"
                                font.pixelSize: 24
                            }
                            Text {
                                text: backend.currentDraw.toFixed(1) + " A"
                                color: "#f5fbff"
                                font.pixelSize: 38
                                font.weight: Font.Bold
                            }
                        }
                    }
                }
            }

            SectionCard {
                Layout.fillWidth: true
                Layout.fillHeight: true

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 20
                    spacing: 10

                    Text {
                        text: "Media"
                        color: "#f2f7fb"
                        font.pixelSize: 28
                        font.weight: Font.Bold
                    }

                    Text {
                        text: backend.songTitle
                        color: "#f8fbff"
                        font.pixelSize: 32
                        font.weight: Font.DemiBold
                    }
                    Text {
                        text: "Artist: " + backend.songArtist
                        color: "#b4c3d3"
                        font.pixelSize: 22
                    }
                    Text {
                        text: "Album: " + backend.songAlbum
                        color: "#8fa4b8"
                        font.pixelSize: 20
                    }

                    Rectangle {
                        Layout.fillWidth: true
                        Layout.topMargin: 10
                        height: 26
                        radius: 13
                        color: "#0d131a"
                        border.color: "#253242"

                        Rectangle {
                            width: Math.max(10, parent.width * backend.songProgress)
                            height: parent.height
                            radius: parent.radius
                            color: "#4da3ff"
                        }

                        Rectangle {
                            x: Math.max(0, Math.min(parent.width - width, parent.width * backend.songProgress - width / 2))
                            width: 18
                            height: 18
                            radius: 9
                            y: 4
                            color: "white"
                        }
                    }

                    Text {
                        text: backend.songPlaying ? "Playing" : "Paused"
                        color: backend.songPlaying ? "#38d46a" : "#ffb347"
                        font.pixelSize: 20
                        font.weight: Font.DemiBold
                    }

                    Text {
                        text: "Mock controls: ← → steer, ↑ ↓ accel, R/F regen, Space play/pause"
                        color: "#6f859a"
                        font.pixelSize: 18
                        wrapMode: Text.WordWrap
                        Layout.topMargin: 8
                    }
                }
            }
        }
    }
}
