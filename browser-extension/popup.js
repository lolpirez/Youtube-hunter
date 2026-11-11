// YouTube Hunter: toolbar popup. Brand, version and a one-line hint.
// v1.7: the ad blocker switch that used to live here is gone.
(() => {
  document.getElementById("ver").textContent = "v" + chrome.runtime.getManifest().version;
})();
