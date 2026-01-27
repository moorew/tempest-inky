import os
import sys
import customtkinter as ctk
from PIL import ImageTk
from main import fetch_weather, create_dashboard, WIDTH, HEIGHT, BASE_DIR # Import BASE_DIR

REFRESH_RATE = 60 

class TempestDesktop(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Tempest Dashboard")
        self.geometry(f"{WIDTH}x{HEIGHT}")
        self.resizable(False, False)
        
        # --- FIXED ICON LOGIC ---
        try:
            # We must use the BASE_DIR to find the icon inside the EXE
            icon_path = os.path.join(BASE_DIR, "assets", "icon.ico")
            self.iconbitmap(icon_path)
        except Exception as e:
            print(f"Could not load icon: {e}")

        try: self.tk.call('tk', 'scaling', 1.0)
        except: pass

        self.img_label = ctk.CTkLabel(self, text="")
        self.img_label.pack(fill="both", expand=True)
        self.update_weather()

    def update_weather(self):
        print("Refreshing...")
        weather = fetch_weather()
        pil_image = create_dashboard(weather, theme_name="desktop")
        tk_image = ctk.CTkImage(light_image=pil_image, dark_image=pil_image, size=(WIDTH, HEIGHT))
        self.img_label.configure(image=tk_image)
        self.img_label.image = tk_image 
        self.after(REFRESH_RATE * 1000, self.update_weather)

if __name__ == "__main__":
    ctk.set_appearance_mode("Dark")
    app = TempestDesktop()
    app.mainloop()