"""Wii cover artwork, Monado icon, and Japanese-capable editor typography."""
from pathlib import Path
import tkinter as tk
from tkinter import ttk,font
from PIL import Image,ImageTk

from runtime_paths import ARTWORK_DIR
ASSETS=ARTWORK_DIR


def configure(window):
    families=set(font.families(window))
    japanese=next((f for f in ('Meiryo','Yu Gothic','MS Gothic') if f in families),'TkDefaultFont')
    ui=next((f for f in ('맑은 고딕','Malgun Gothic','Segoe UI') if f in families),'TkDefaultFont')
    style=ttk.Style(window);style.theme_use('clam')
    window.configure(background='#e9eef1');window.minsize(1060,760)
    window.option_add('*Font',(ui,10))
    style.configure('.',font=(ui,10),background='#e9eef1',foreground='#233a49')
    style.configure('TFrame',background='#e9eef1')
    style.configure('TLabel',background='#e9eef1')
    style.configure('TButton',padding=(14,8),background='#f8fafb',bordercolor='#bccbd3',relief='flat')
    style.map('TButton',background=[('active','#dceaf0')])
    style.configure('Accent.TButton',background='#ac2837',foreground='white',bordercolor='#ac2837')
    style.map('Accent.TButton',background=[('active','#cc3a46'),('pressed','#8b202c')])
    style.configure('Treeview',font=(japanese,10),rowheight=29,background='#fcfdfd',fieldbackground='#fcfdfd',borderwidth=0)
    style.map('Treeview',background=[('selected','#264f65')],foreground=[('selected','white')])
    style.configure('Treeview.Heading',font=(ui,10,'bold'),background='#dce5eb',padding=(8,7),relief='flat')
    style.configure('TNotebook',background='#e9eef1',borderwidth=0)
    style.configure('TNotebook.Tab',padding=(16,9),background='#d8e2e8')
    style.map('TNotebook.Tab',background=[('selected','#fcfdfd')],foreground=[('selected','#982b37')])
    style.configure('TEntry',fieldbackground='#fcfdfd',padding=6)
    style.configure('TCombobox',padding=5)
    if (ASSETS/'monado.ico').is_file():window.iconbitmap(str(ASSETS/'monado.ico'))
    return japanese


class CoverHeader(tk.Canvas):
    def __init__(self,parent,platform='Wii'):
        self.platform=platform
        super().__init__(parent,height=154,background='#172f41',highlightthickness=0)
        with Image.open(ASSETS/'wii_cover.jpg') as source:self.cover=source.convert('RGB').copy()
        self.photo=None;self.bind('<Configure>',self.redraw)

    def redraw(self,event):
        width=max(event.width,1);height=154
        # Keep the supplied cover intact: scale proportionally, never crop its Monado.
        art=self.cover.resize((round(self.cover.width*height/self.cover.height),height),Image.Resampling.LANCZOS)
        self.photo=ImageTk.PhotoImage(art,master=self)
        self.delete('all');self.create_image(width,0,anchor='ne',image=self.photo)
        self.create_rectangle(0,0,7,height,fill='#b82c3c',outline='')
        self.create_text(28,29,anchor='w',text='X E N O B L A D E',fill='#ffffff',font=('Segoe UI',26,'bold'))
        self.create_text(30,64,anchor='w',text='CHRONICLES   /   '+self.platform,fill='#bed4df',font=('Segoe UI',12))
        self.create_text(30,106,anchor='w',text='한글화 워크벤치',fill='#ffffff',font=('맑은 고딕',12,'bold'))
        self.create_text(30,132,anchor='w',text='텍스트 · 컷신 자막 · 이미지',fill='#bed4df',font=('맑은 고딕',10))
        self.create_line(0,153,width,153,fill='#b82c3c',width=2)
