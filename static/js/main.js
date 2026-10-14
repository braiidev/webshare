import { initApp } from "./ui.js";

initApp().catch((error) => {
  console.error("[webshare] error de arranque", error);
});
