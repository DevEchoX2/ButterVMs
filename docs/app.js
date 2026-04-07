const launchButton = document.getElementById("launch-server");
const serverUrlInput = document.getElementById("server-url");

function normalizeUrl(value) {
	const trimmed = value.trim();
	if (!trimmed) {
		return "http://localhost:8000";
	}
	if (trimmed.startsWith("http://") || trimmed.startsWith("https://")) {
		return trimmed;
	}
	return `https://${trimmed}`;
}

if (launchButton && serverUrlInput) {
	launchButton.addEventListener("click", () => {
		const target = normalizeUrl(serverUrlInput.value);
		window.open(target, "_blank", "noopener,noreferrer");
	});
}

document.querySelectorAll("[data-launch-path]").forEach((button) => {
	button.addEventListener("click", () => {
		const baseUrl = normalizeUrl(serverUrlInput ? serverUrlInput.value : "http://localhost:8000");
		const path = button.getAttribute("data-launch-path") || "/";
		window.open(`${baseUrl}${path}`, "_blank", "noopener,noreferrer");
	});
});
