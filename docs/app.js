const openButton = document.getElementById("open-live");
const apiInput = document.getElementById("api-url");

openButton.addEventListener("click", () => {
  const value = apiInput.value.trim();
  if (!value) {
    alert("Enter your full stack URL first.");
    return;
  }

  window.open(value, "_blank", "noopener,noreferrer");
});
