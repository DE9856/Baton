// Records a voice answer in the browser and sends it to the server. The file input in each form is the fallback.
(function () {
  if (!navigator.mediaDevices || !window.MediaRecorder) return;
  document.querySelectorAll("[data-voice-form]").forEach(function (form) {
    var button = form.querySelector("[data-record]");
    var status = form.querySelector("[data-voice-status]");
    if (!button) return;
    button.hidden = false;
    var recorder = null;
    var parts = [];
    var seconds = 0;
    var timer = null;

    function say(text) { status.textContent = text; }

    function send(blob) {
      say("Transcribing on the server. This can take a little while.");
      button.disabled = true;
      var data = new FormData();
      data.append("file", blob, "answer.webm");
      fetch(form.action, { method: "POST", body: data, credentials: "same-origin" })
        .then(function (r) {
          if (!r.ok) throw new Error("Upload failed");
          window.location = r.url;
        })
        .catch(function () { say("That didn't upload. Please try again, or type your answer."); button.disabled = false; });
    }

    button.addEventListener("click", function () {
      if (recorder && recorder.state === "recording") { recorder.stop(); return; }
      navigator.mediaDevices.getUserMedia({ audio: true }).then(function (stream) {
        parts = [];
        recorder = new MediaRecorder(stream);
        recorder.ondataavailable = function (e) { if (e.data.size) parts.push(e.data); };
        recorder.onstop = function () {
          clearInterval(timer);
          stream.getTracks().forEach(function (t) { t.stop(); });
          button.textContent = "Record a voice answer";
          send(new Blob(parts, { type: recorder.mimeType || "audio/webm" }));
        };
        recorder.start();
        seconds = 0;
        button.textContent = "Stop and send";
        say("Recording: 0 s");
        timer = setInterval(function () {
          seconds += 1;
          say("Recording: " + seconds + " s");
          if (seconds >= 290) recorder.stop();
        }, 1000);
      }).catch(function () { say("The microphone isn't available. You can attach a recording or type your answer."); });
    });
  });
})();
