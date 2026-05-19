import os
import threading

import customtkinter as ctk
from main import BASE_DIR, HEIGHT, WIDTH, create_dashboard, fetch_weather

REFRESH_RATE = 60


class TempestDesktop(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Tempest Dashboard")
        self.geometry(f"{WIDTH}x{HEIGHT}")
        self.resizable(False, False)

        try:
            icon_path = os.path.join(BASE_DIR, "assets", "icon.ico")
            self.iconbitmap(icon_path)
        except Exception as e:
            print(f"Could not load icon: {e}")

        try:
            self.tk.call("tk", "scaling", 1.0)
        except Exception as e:
            print(f"Could not set Tk scaling: {e}")

        self.img_label = ctk.CTkLabel(self, text="")
        self.img_label.pack(fill="both", expand=True)
        self.refresh_in_progress = False
        self.update_weather()

    def update_weather(self):
        if self.refresh_in_progress:
            self.after(REFRESH_RATE * 1000, self.update_weather)
            return

        print("Refreshing...")
        self.refresh_in_progress = True
        worker = threading.Thread(target=self.fetch_and_render_weather, daemon=True)
        worker.start()

    def fetch_and_render_weather(self):
        try:
            weather = fetch_weather()
            pil_image = create_dashboard(weather, theme_name="desktop")
        except Exception as e:
            print(f"Refresh failed: {e}")
            pil_image = create_dashboard(None, theme_name="desktop")
        self.after(0, self.apply_weather_image, pil_image)

    def apply_weather_image(self, pil_image):
        tk_image = ctk.CTkImage(light_image=pil_image, dark_image=pil_image, size=(WIDTH, HEIGHT))
        self.img_label.configure(image=tk_image)
        self.img_label.image = tk_image
        self.refresh_in_progress = False
        self.after(REFRESH_RATE * 1000, self.update_weather)


if __name__ == "__main__":
    ctk.set_appearance_mode("Dark")
    app = TempestDesktop()
    app.mainloop()
