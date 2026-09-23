"""Shared standalone launcher for the Wii and Nintendo 3DS editors."""
import argparse
import importlib
import json
from pathlib import Path
import sys
import tkinter as tk
from tkinter import ttk
import wii_editor
import n3ds_editor
from runtime_paths import APP_ROOT,ARTWORK_DIR,SETTINGS_DIR

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--wii',action='store_true')
    parser.add_argument('--3ds',dest='n3ds',action='store_true')
    parser.add_argument('--smoke-test',action='store_true')
    parser.add_argument('--report',type=Path)
    args=parser.parse_args()
    if args.smoke_test:
        report={'frozen':bool(getattr(sys,'frozen',False)),'root':str(APP_ROOT),'settings':str(SETTINGS_DIR),'editors':[]}
        try:
            for p in ('monado.ico','wii_cover.jpg'):assert (ARTWORK_DIR/p).is_file(),p
            from wii_editor_workflow import font_mapping
            mapping=font_mapping(APP_ROOT/'resources/fonts/xbsystem.tbl')
            # Exercise Tk, Pillow/ImageTk, resources, and both complete UI constructors.
            for mode,module in [('wii',wii_editor),('3ds',n3ds_editor)]:
                window=tk.Tk();window.withdraw()
                try:
                    app=module.Editor(window,settings_path=None)
                    if mode=='wii':
                        app.mapping=mapping
                        assert app.display_text('笑庁 1')=='아츠 1'
                    window.update_idletasks()
                    report['editors'].append({'mode':mode,'ui_constructed':True,'module':module.__file__})
                finally:window.destroy()
            for name in ('wii_font','n3ds_font','wii_texture_repack','n3ds_texture_repack','wii_layout','n3ds_layout','compare_fonts','extract_font','rev_resource_names','font_metadata'):
                importlib.import_module(name)
            report['ok']=True
        except Exception as exc:
            import traceback
            report.update(ok=False,error=str(exc),traceback=traceback.format_exc())
        if args.report:args.report.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        return 0 if report['ok'] else 1
    mode='wii' if args.wii else '3ds' if args.n3ds else None
    if mode is None:
        from wii_editor_theme import configure,CoverHeader
        chooser=tk.Tk();configure(chooser);chooser.title('Xenoblade Editors')
        chooser.minsize(650,400);chooser.geometry('760x460')
        CoverHeader(chooser).pack(fill='x')
        chosen=[]
        frame=ttk.Frame(chooser,padding=24);frame.pack(fill='both',expand=True)
        ttk.Label(frame,text='편집할 게임을 선택하세요',font=('Malgun Gothic',17,'bold')).pack(pady=16)
        def select(value):chosen.append(value);chooser.destroy()
        ttk.Button(frame,text='Xenoblade Chronicles · Wii',command=lambda:select('wii'),style='Accent.TButton').pack(fill='x',pady=8)
        ttk.Button(frame,text='Xenoblade Chronicles · Nintendo 3DS',command=lambda:select('3ds'),style='Accent.TButton').pack(fill='x',pady=8)
        chooser.mainloop()
        if not chosen:return 0
        mode=chosen[0]
    window=tk.Tk()
    (wii_editor.Editor if mode=='wii' else n3ds_editor.Editor)(window)
    window.mainloop()
    return 0

if __name__=='__main__':sys.exit(main())
