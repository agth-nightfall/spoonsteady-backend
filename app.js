const BROKER_URL = "wss://broker.hivemq.com:8884/mqtt";
const TOPIC_FEED = "spoonsteady/telemetry";

let client = null;
let currentRole = "sensor"; // 'sensor' atau 'monitor'
let audioCtx = null;
let totalScoops = 0;
let isInScoopMotion = false;
let lastPublish = 0;

function setRole(role) {
  currentRole = role;
  document.getElementById("btnRoleSensor").classList.toggle("active", role === "sensor");
  document.getElementById("btnRoleMonitor").classList.toggle("active", role === "monitor");
  document.getElementById("panelSensor").style.display = role === "sensor" ? "block" : "none";
  document.getElementById("panelMonitor").style.display = role === "monitor" ? "block" : "none";
  writeLog(`Beralih ke mode: ${role === "sensor" ? "HP 1 (Sendok/Tangan)" : "HP 2 (Meja Makan)"}`);
}

function writeLog(msg) {
  const log = document.getElementById("logText");
  const time = new Date().toLocaleTimeString();
  log.innerHTML = `[${time}] ${msg}<br>` + log.innerHTML;
}

function initApp() {
  if (!audioCtx) {
    audioCtx = new (window.AudioContext || window.webkitAudioContext)();
  }

  document.getElementById("btnStart").disabled = true;
  document.getElementById("btnStart").innerText = "Menghubungkan MQTT...";

  client = mqtt.connect(BROKER_URL);

  client.on("connect", () => {
    document.getElementById("connStatus").className = "badge connected";
    document.getElementById("connStatus").innerText = "Online";
    document.getElementById("btnStart").innerText = "Sistem Aktif";
    writeLog("Terhubung ke MQTT Broker HiveMQ.");

    client.subscribe(TOPIC_FEED);

    if (currentRole === "sensor") {
      requestSensors();
    }
  });

  client.on("message", (topic, payload) => {
    try {
      const data = JSON.parse(payload.toString());
      if (topic === TOPIC_FEED && currentRole === "monitor") {
        updateMonitorView(data);
      }
    } catch (e) {
      console.error(e);
    }
  });
}

function requestSensors() {
  if (typeof DeviceMotionEvent !== "undefined" && typeof DeviceMotionEvent.requestPermission === "function") {
    DeviceMotionEvent.requestPermission().then((res) => {
      if (res === "granted") startTelemetry();
    }).catch(console.error);
  } else {
    startTelemetry();
  }
}

let currentPitch = 0;

function startTelemetry() {
  writeLog("Mulai membaca sensor akselerometer & orientasi lengan...");

  window.addEventListener("deviceorientation", (e) => {
    if (currentRole !== "sensor") return;
    currentPitch = Math.round(e.beta || 0);
    document.getElementById("tiltSensorVal").innerText = `${currentPitch}°`;
  });

  window.addEventListener("devicemotion", (e) => {
    if (currentRole !== "sensor") return;

    const acc = e.accelerationIncludingGravity;
    if (!acc) return;

    // Menghitung deviasi getaran dari gravitasi bumi 9.81 m/s^2
    const totalMag = Math.sqrt((acc.x || 0)**2 + (acc.y || 0)**2 + (acc.z || 0)**2);
    const rmsDeviation = Math.abs(totalMag - 9.81);

    document.getElementById("rmsSensorVal").innerText = `${rmsDeviation.toFixed(2)} m/s²`;

    // Kirim data via MQTT setiap 200 ms
    const now = Date.now();
    if (now - lastPublish > 200) {
      lastPublish = now;
      const payload = {
        rms: Number(rmsDeviation.toFixed(2)),
        pitch: currentPitch
      };
      client.publish(TOPIC_FEED, JSON.stringify(payload));
    }
  });
}

function updateMonitorView(data) {
  const rms = data.rms;
  const pitch = data.pitch;

  document.getElementById("monitorRms").innerText = `${rms.toFixed(2)} m/s²`;

  // Kalkulasi Skor Kestabilan (0 - 100%)
  // Makin tinggi getaran RMS, kestabilan makin rendah
  let stability = Math.max(10, Math.round(100 - (rms * 28)));
  document.getElementById("stabilityScore").innerText = stability;

  const indicator = document.getElementById("tremorIndicator");
  const circle = document.getElementById("scoreCircle");

  if (rms < 0.8) {
    indicator.className = "status-alert calm";
    indicator.innerText = "Kondisi Tangan Tenang & Stabil";
    circle.style.borderColor = "var(--safe)";
  } else if (rms >= 0.8 && rms < 2.0) {
    indicator.className = "status-alert mild";
    indicator.innerText = "Getaran Ringan Terdeteksi";
    circle.style.borderColor = "var(--warning)";
  } else {
    indicator.className = "status-alert tremor";
    indicator.innerText = "⚠️ Tremor Signifikan! Makanan Berisiko Tumpah";
    circle.style.borderColor = "var(--danger)";
    playChime(); // Peringatan audio lembut
  }

  // Deteksi siklus suapan makanan (lengan naik > 40 derajat lalu turun kembali)
  if (pitch > 40 && !isInScoopMotion) {
    isInScoopMotion = true;
  } else if (pitch < 20 && isInScoopMotion) {
    isInScoopMotion = false;
    totalScoops++;
    document.getElementById("scoopCount").innerText = `${totalScoops} Kali`;
    writeLog(`Suapan ke-${totalScoops} selesai dieksekusi.`);
  }
}

// Bunyi nada lembut untuk membantu menenangkan ritme gerak pengguna
function playChime() {
  if (!audioCtx) return;
  const osc = audioCtx.createOscillator();
  const gain = audioCtx.createGain();

  osc.type = "sine";
  osc.frequency.setValueAtTime(520, audioCtx.currentTime);

  gain.gain.setValueAtTime(0.15, audioCtx.currentTime);
  gain.gain.exponentialRampToValueAtTime(0.001, audioCtx.currentTime + 0.6);

  osc.connect(gain);
  gain.connect(audioCtx.destination);

  osc.start();
  osc.stop(audioCtx.currentTime + 0.6);
}
