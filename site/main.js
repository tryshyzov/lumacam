// Language switching and download links from the latest GitHub release.

const REPO = "tryshyzov/lumacam";
const RELEASES = `https://github.com/${REPO}/releases/latest`;

// Russian is the page's own text; this holds English for every data-i18n key.
const EN = {
  nav_how: "How it works",
  nav_download: "Download",
  nav_faq: "FAQ",
  eyebrow: "Free and open source",
  hero_title: "Your phone's camera as a webcam",
  hero_lead: "Connect an Android phone over USB or Wi-Fi and use its camera in Zoom, Telegram, Discord and OBS. Focus, exposure, ISO and white balance, right from your computer.",
  hero_cta: "Download",
  hero_source: "Source code",
  hero_platforms: "Windows 10/11 · macOS 13+ (Apple silicon and Intel) · Linux · Android 8+",
  shot_alt: "LumaLink Studio window with the camera settings",
  how_title: "How it works",
  step1_title: "The phone films",
  step1_text: "The LumaLink Companion app takes the phone's camera video and compresses it with the hardware H.264 encoder.",
  step2_title: "Video goes over USB or Wi-Fi",
  step2_text: "By cable through USB tethering or USB debugging, with no internet needed. Over Wi-Fi within one home network.",
  step3_title: "The computer shows it",
  step3_text: "LumaLink Studio shows the picture and passes it to a virtual camera that Zoom, Telegram, Discord, browsers and OBS can pick.",
  features_title: "Features",
  f1_title: "Manual camera controls",
  f1_text: "Autofocus or manual focus, exposure, ISO, white balance, zoom, rear or front camera, all from the computer.",
  f2_title: "Up to 1080p at 60 fps",
  f2_text: "480p, 720p and 1080p profiles at 30 or 60 frames per second. The phone tells where its camera really gives 60, and the app offers only what is available.",
  f3_title: "Its own virtual camera",
  f3_text: "On Windows the installer adds LumaLink Camera, with no kernel driver. On a Mac the camera goes through OBS Virtual Camera for now.",
  f4_title: "USB past a VPN",
  f4_text: "When a VPN blocks the local network, the app switches to ADB by itself and the connection goes straight over the cable.",
  f5_title: "Low latency",
  f5_text: "The phone sends only the freshest frame and waits for it to be acknowledged, so the video never falls behind.",
  f6_title: "Pause and idle screen",
  f6_text: "Freeze the picture with one button. While no phone is connected, the virtual camera shows a neat idle screen.",
  download_title: "Download",
  download_lead: "You need two apps: LumaLink Studio on the computer and LumaLink Companion on the phone.",
  dl_windows: "Installer with the LumaLink Camera virtual camera. Windows 10 and 11.",
  dl_mac_arm_title: "Mac with Apple silicon",
  dl_mac_arm: "M1 or later. macOS 13 Ventura or later.",
  dl_mac_intel_title: "Mac with Intel",
  dl_mac_intel: "macOS 13 Ventura or later.",
  dl_linux: "x86-64, Ubuntu 22.04 or later. The virtual camera works through v4l2loopback.",
  dl_linux_deb: ".deb package",
  dl_linux_arch: "Arch Linux package",
  dl_android_title: "Android phone",
  dl_android: "LumaLink Companion. Android 8.0 or later.",
  dl_button: "Download",
  dl_note: "The iPhone app is not ready yet. The Mac version is not signed with an Apple certificate: open it the first time with right-click → Open.",
  dl_soon: "Coming soon",
  dl_soon_note: "In the next release",
  dl_version: "Version {version}.",
  setup_title: "How to connect",
  setup_usb_title: "Over USB",
  setup_usb_1: "Connect the phone with a cable.",
  setup_usb_2: "On Windows turn on USB tethering on the phone; on a Mac, USB debugging: macOS does not understand most phones' tethering.",
  setup_usb_3: "Open LumaLink Companion: the phone waits for the computer by itself, there is nothing to tap.",
  setup_usb_4: "Open LumaLink Studio: it finds the phone and connects by itself. The list shows which link it uses.",
  setup_wifi_title: "Over Wi-Fi",
  setup_wifi_1: "The phone and the computer must be on the same network.",
  setup_wifi_2: "Open LumaLink Companion on the phone and LumaLink Studio on the computer.",
  setup_wifi_3: "In LumaLink Studio's list, choose the phone marked Wi-Fi.",
  setup_wifi_4: "The first time, allow the connection on the phone's screen; after that the computer connects by itself.",
  faq_title: "FAQ",
  q1: "Is it secure?",
  a1: "The video is not encrypted. Over Wi-Fi a new computer has to be allowed on the phone once, so other devices cannot reach the camera without you noticing, but someone on the same network can still read the traffic. Use Wi-Fi only on a trusted network and close the phone app when you do not need the camera. Over USB the video does not leave the cable.",
  q2: "Why does it not connect with a VPN on?",
  a2: "A VPN that blocks the local network stops traffic to the phone. For USB, turn on USB debugging on the phone: the app goes through ADB, past the VPN. For Wi-Fi, add LumaLink Studio to the VPN's exceptions.",
  q3: "Is there any delay?",
  a3: "A little, always: the camera, encoding, transfer and display all take time. LumaLink keeps it minimal by never queueing frames.",
  q4: "Does my phone support manual focus and ISO?",
  a4: "It depends on the phone. The app applies only what the camera supports and keeps settings within its ranges.",
  q5: "What about iPhone?",
  a5: "An iOS app is planned but not built yet.",
  footer_license: "Apache License 2.0",
};

const RU_EXTRA = {
  dl_soon: "Скоро",
  dl_soon_note: "В следующем релизе",
  dl_version: "Версия {version}.",
};

const elements = [...document.querySelectorAll("[data-i18n]")];
// Keep the page's Russian text so switching back needs no second copy.
const RU = Object.fromEntries(elements.map((el) => [el.dataset.i18n, el.textContent]));
Object.assign(RU, RU_EXTRA);
RU.shot_alt = document.getElementById("shot").alt;

let language = "ru";
let release = null;

function t(key, values = {}) {
  const text = (language === "en" ? EN : RU)[key] ?? RU[key] ?? key;
  return text.replace(/\{(\w+)\}/g, (_, name) => values[name] ?? "");
}

function setLanguage(next) {
  language = next;
  document.documentElement.lang = next;
  for (const el of elements) el.textContent = t(el.dataset.i18n);
  const shot = document.getElementById("shot");
  shot.src = `assets/app-${next}.jpg`;
  shot.alt = t("shot_alt");
  const toggle = document.getElementById("lang-toggle");
  toggle.textContent = next === "en" ? "RU" : "EN";
  toggle.setAttribute("aria-label", next === "en" ? "Переключить на русский" : "Switch to English");
  document.title = next === "en"
    ? "LumaLink Studio — your phone's camera as a webcam"
    : "LumaLink Studio — камера телефона как веб-камера";
  showRelease();
  try { localStorage.setItem("language", next); } catch { /* private mode */ }
}

// Which download fits this visitor; browsers report every Mac as Intel, so a
// Mac gets both Mac options without one marked.
function suggestedAsset() {
  const ua = navigator.userAgent;
  if (/Android/i.test(ua)) return "android";
  if (/Windows/i.test(ua)) return "windows";
  if (/Linux/i.test(ua)) return "linux";
  return null;
}

const ASSETS = {
  windows: /Setup.*\.exe$/i,
  "mac-arm": /macos-apple-silicon\.dmg$/i,
  "mac-intel": /macos-intel\.dmg$/i,
  linux: /x86_64\.AppImage$/i,
  "linux-deb": /_amd64\.deb$/i,
  "linux-arch": /x86_64\.pkg\.tar\.zst$/i,
  android: /\.apk$/i,
};

function showRelease() {
  const version = document.getElementById("version");
  version.textContent = release ? t("dl_version", { version: release.tag_name.replace(/^v/, "") }) : "";
  for (const card of document.querySelectorAll(".download")) {
    const link = card.querySelector("a");
    const pattern = ASSETS[card.dataset.asset];
    const asset = release?.assets.find((item) => pattern.test(item.name));
    card.querySelector("small")?.remove();
    if (!release || asset) {
      link.href = asset ? asset.browser_download_url : RELEASES;
      link.classList.remove("disabled");
      link.textContent = t("dl_button");
      if (asset) {
        addNote(card, `${asset.name} · ${(asset.size / 1048576).toFixed(0)} MB`);
      }
    } else {
      link.href = RELEASES;
      link.classList.add("disabled");
      link.textContent = t("dl_soon");
      addNote(card, t("dl_soon_note"));
    }
    // Second files in a card, such as the .deb next to the AppImage.
    for (const extra of card.querySelectorAll("[data-asset-link]")) {
      const found = release?.assets.find((item) => ASSETS[extra.dataset.assetLink].test(item.name));
      extra.href = found ? found.browser_download_url : RELEASES;
      extra.hidden = Boolean(release) && !found;
    }
  }
}

// The file name and size, or why there is none, just above the button.
function addNote(card, text) {
  const note = document.createElement("small");
  note.textContent = text;
  card.querySelector("a").before(note);
}

async function loadRelease() {
  try {
    const response = await fetch(`https://api.github.com/repos/${REPO}/releases/latest`);
    if (!response.ok) return;
    release = await response.json();
    showRelease();
  } catch {
    // Offline or rate-limited: the buttons keep pointing at the releases page.
  }
}

const suggested = suggestedAsset();
if (suggested) document.querySelector(`.download[data-asset="${suggested}"]`)?.classList.add("suggested");

let saved = null;
try { saved = localStorage.getItem("language"); } catch { /* private mode */ }
const initial = saved || (navigator.language?.toLowerCase().startsWith("ru") ? "ru" : "en");
if (initial !== "ru") setLanguage(initial);

document.getElementById("lang-toggle").addEventListener("click", () => {
  setLanguage(language === "en" ? "ru" : "en");
});

loadRelease();
