"""Desktop 3DS localization editor. Launch with pythonw, no command-line workflow."""
import json
import queue
import threading
from pathlib import Path
import tkinter as tk
from tkinter import ttk,filedialog,messagebox
from PIL import Image,ImageTk
from n3ds_editor_core import Project,documents,resolve,text_rows,edit_text
from n3ds_editor_images import catalog,preview,replace
from wii_editor_theme import configure,CoverHeader

from runtime_paths import APP_ROOT,SETTINGS_DIR
LAB=APP_ROOT


class Editor:
    def __init__(self,window,settings_path=SETTINGS_DIR/'n3ds_editor_settings.json'):
        self.settings_path=Path(settings_path) if settings_path is not None else None
        self.saved_root=''
        if self.settings_path is not None:
            try:
                settings=json.loads(self.settings_path.read_text(encoding='utf-8'))
                if isinstance(settings,dict) and isinstance(settings.get('files_root'),str):
                    self.saved_root=settings['files_root']
            except (OSError,ValueError):pass
        self.window=window;window.title('Xenoblade 3DS · 한글화 에디터')
        window.geometry(f'{min(1240,window.winfo_screenwidth()-80)}x{min(820,window.winfo_screenheight()-100)}')
        self.japanese_font=configure(window)
        self.cover_header=CoverHeader(window,platform='New Nintendo 3DS');self.cover_header.pack(fill='x')
        self.project=None;self.items={};self.doc=None;self.rows=[];self.images=[];self.mapping={}
        self.queue=queue.Queue();self.busy=False;self.row_key=None;self.photo=None
        self.status=tk.StringVar(value='3DS 게임 루트를 열어 주세요. 원본은 읽기 전용입니다.')
        bar=ttk.Frame(window,padding=10);bar.pack(fill='x')
        for text,cmd in [('게임 루트 열기',self.open_root),
                         ('문자 매핑 불러오기',self.load_mapping),('수정본 저장',self.save)]:
            ttk.Button(bar,text=text,command=cmd,style='Accent.TButton' if text=='수정본 저장' else 'TButton').pack(side='left',padx=4)
        ttk.Label(bar,text='3DS  /  원본 보존',foreground='#386b56').pack(side='right')
        main=ttk.Panedwindow(window,orient='horizontal');main.pack(fill='both',expand=True,padx=12)
        left=ttk.Frame(main);right=ttk.Frame(main);main.add(left,weight=1);main.add(right,weight=3)
        ttk.Label(left,text='영역 → 내부 파일',padding=5).pack(anchor='w')
        treebox=ttk.Frame(left);treebox.pack(fill='both',expand=True)
        self.tree=ttk.Treeview(treebox,show='tree',selectmode='browse')
        scroll=ttk.Scrollbar(treebox,orient='vertical',command=self.tree.yview);scroll.pack(side='right',fill='y')
        self.tree.configure(yscrollcommand=scroll.set);self.tree.pack(fill='both',expand=True)
        self.tree.bind('<<TreeviewOpen>>',self.expand);self.tree.bind('<<TreeviewSelect>>',self.select_document)
        self.title=tk.StringVar(value='파일을 선택하면 자막·문자열·이미지가 표시됩니다.')
        ttk.Label(right,textvariable=self.title,padding=8,wraplength=760,font=(self.japanese_font,10)).pack(fill='x')
        tabs=ttk.Notebook(right);tabs.pack(fill='both',expand=True)
        texttab=ttk.Frame(tabs,padding=8);imagetab=ttk.Frame(tabs,padding=8)
        tabs.add(texttab,text='  문자열 / 자막  ');tabs.add(imagetab,text='  이미지  ')
        searchbar=ttk.Frame(texttab);searchbar.pack(fill='x')
        ttk.Label(searchbar,text='현재 파일에서 찾기').pack(side='left')
        self.search=tk.StringVar();box=ttk.Entry(searchbar,textvariable=self.search);box.pack(side='left',fill='x',expand=True,padx=8)
        box.bind('<Return>',lambda e:self.fill_rows());ttk.Button(searchbar,text='찾기',command=self.fill_rows).pack(side='left')
        tablebox=ttk.Frame(texttab)
        self.table=ttk.Treeview(tablebox,columns=('id','context','text'),show='headings',height=9,selectmode='browse')
        scroll=ttk.Scrollbar(tablebox,orient='vertical',command=self.table.yview);scroll.pack(side='right',fill='y')
        self.table.configure(yscrollcommand=scroll.set)
        for col,title,width in [('id','번호',70),('context','행 / 열 / 시간',155),('text','문자열',500)]:
            self.table.heading(col,text=title);self.table.column(col,width=width,stretch=col=='text')
        self.table.pack(fill='both',expand=True);self.table.bind('<<TreeviewSelect>>',self.select_row)
        self.original=tk.StringVar();original_label=ttk.Label(texttab,textvariable=self.original,wraplength=770,font=(self.japanese_font,11))
        self.text=tk.Text(texttab,height=4,wrap='word',font=(self.japanese_font,12),undo=True,
                          background='#fcfdfd',foreground='#233a49',insertbackground='#ac2837',
                          selectbackground='#264f65',selectforeground='white',relief='flat',
                          highlightthickness=1,highlightbackground='#bccbd3',highlightcolor='#ac2837',padx=12,pady=10)
        self.text.edit_modified(False)
        buttons=ttk.Frame(texttab);buttons.pack(side='bottom',fill='x')
        self.text.pack(side='bottom',fill='x',pady=8)
        original_label.pack(side='bottom',fill='x')
        tablebox.pack(fill='both',expand=True,pady=8)
        ttk.Button(buttons,text='문장 반영',command=self.apply_row,style='Accent.TButton').pack(side='left')
        ttk.Button(buttons,text='텍스트 상자 속성',command=self.edit_layout).pack(side='left',padx=6)
        ttk.Label(buttons,text='줄바꿈 제어문자는 원문 규칙을 유지하세요. UTF-8 한글 저장과 게임 폰트 글리프 준비는 별개입니다.',wraplength=570).pack(side='left',padx=12)
        self.image_list=ttk.Combobox(imagetab,state='readonly');self.image_list.pack(fill='x')
        self.image_list.bind('<<ComboboxSelected>>',lambda e:self.show_image())
        self.canvas=tk.Canvas(imagetab,bg='#1b303e',highlightthickness=0,height=280);self.canvas.pack(fill='both',expand=True,pady=10)
        self.image_status=tk.StringVar();ttk.Label(imagetab,textvariable=self.image_status).pack(fill='x')
        imagebuttons=ttk.Frame(imagetab);imagebuttons.pack(fill='x',pady=8)
        ttk.Button(imagebuttons,text='PNG 내보내기',command=self.export_image).pack(side='left',padx=4)
        ttk.Button(imagebuttons,text='PNG로 교체',command=self.import_image).pack(side='left',padx=4)
        ttk.Button(imagebuttons,text='폰트 문자표 / 폭',command=self.edit_font).pack(side='left',padx=4)
        self.lossy=tk.BooleanVar(value=False)
        ttk.Checkbutton(imagebuttons,text='손실 변환 허용(ETC1·ETC1A4)',variable=self.lossy).pack(side='left',padx=12)
        ttk.Label(window,textvariable=self.status,anchor='w',padding=10).pack(side='bottom',fill='x',before=main)
        window.protocol('WM_DELETE_WINDOW',self.close);window.after(100,self.poll)
        if self.saved_root:window.after_idle(self.restore_root)

    def error(self,exc):messagebox.showerror('작업을 완료하지 못했습니다',str(exc),parent=self.window)

    def run(self,work,done):
        if self.busy:return
        self.busy=True;self.window.config(cursor='watch')
        def worker():
            try:self.queue.put((done,work(),None))
            except Exception as exc:self.queue.put((done,None,exc))
        threading.Thread(target=worker,daemon=True).start()

    def poll(self):
        try:
            done,value,error=self.queue.get_nowait();self.busy=False;self.window.config(cursor='')
            if error:self.error(error);self.status.set('작업 실패. 원본은 변경하지 않았습니다.')
            else:
                try:done(value)
                except Exception as exc:self.error(exc)
        except queue.Empty:pass
        self.window.after(100,self.poll)

    def pending_row(self):
        if not self.text.edit_modified():return True
        return self.apply_row()

    def open_root(self):
        if self.busy or not self.pending_row():return
        if self.project and self.project.dirty and not messagebox.askyesno('미저장 수정','현재 수정 내용을 닫고 다른 루트를 열까요?'):return
        initial=str(self.project.root) if self.project else self.saved_root
        options={'initialdir':initial} if initial and Path(initial).is_dir() else {}
        path=filedialog.askdirectory(title='3DS 게임 ExtractedRomFS 폴더 또는 그 상위 폴더',**options)
        if path:self.run(lambda:Project(path),self.remember_project)

    def remember_project(self,project):
        self.install_project(project)
        self.saved_root=str(project.root)
        if self.settings_path is not None:
            try:
                temporary=self.settings_path.with_suffix('.json.tmp')
                temporary.write_text(json.dumps({'files_root':self.saved_root},ensure_ascii=False,indent=2),encoding='utf-8')
                temporary.replace(self.settings_path)
            except OSError as exc:
                self.error(f'루트는 열었지만 다음 실행을 위한 경로 저장에 실패했습니다: {exc}')

    def restore_root(self):
        if self.project or self.busy:return
        path=self.saved_root
        self.status.set('기억한 3DS 루트를 여는 중… '+path)
        def work():
            try:return Project(path),None
            except Exception as exc:return None,str(exc)
        def done(result):
            project,error=result
            if project is not None:self.install_project(project)
            else:self.status.set(f'기억한 루트를 열 수 없습니다: {path} · 디스크 연결 후 게임 루트 열기를 눌러 주세요. ({error})')
        self.run(work,done)

    def install_project(self,project):
        self.project=project;self.tree.delete(*self.tree.get_children());self.items={};self.doc=None;self.row_key=None
        self.table.delete(*self.table.get_children());self.rows=[];self.images=[]
        self.text.delete('1.0','end');self.text.edit_modified(False);self.canvas.delete('all')
        self.image_list['values']=[];self.original.set('')
        areas=sorted({name.split('/')[0] if '/' in name else '(루트)' for name in project.files})
        for area in areas:
            iid=self.tree.insert('','end',text=area);self.items[iid]=('area',area)
            self.tree.insert(iid,'end',text='불러오기…')
        self.status.set(str(project.root)+' · 영역을 펼쳐 선택하세요.')


    def expand(self,event=None):
        if self.busy:return
        iid=self.tree.focus();item=self.items.get(iid)
        if not item or item[0] not in ('area','entry'):return
        children=self.tree.get_children(iid)
        if not children or self.tree.item(children[0],'text')!='불러오기…':return
        if item[0]=='area':
            self.tree.delete(*children)
            for name in self.project.files:
                area=name.split('/')[0] if '/' in name else '(루트)'
                if area!=item[1]:continue
                child=self.tree.insert(iid,'end',text=name);self.items[child]=('entry',name)
                self.tree.insert(child,'end',text='불러오기…')
        else:
            key=item[1]
            def done(rows):
                self.tree.delete(*self.tree.get_children(iid))
                for d in rows:
                    child=self.tree.insert(iid,'end',text=f"{d['name']} · {d['kind']}")
                    self.items[child]=('doc',key,d)
                self.status.set(f'{len(rows)}개 내부 파일을 찾았습니다.')
            self.run(lambda:documents(self.project.read(key),key),done)

    def select_document(self,event=None):
        if self.busy:return
        selection=self.tree.selection();item=self.items.get(selection[0]) if selection else None
        if not item or item[0]!='doc' or not self.pending_row():return
        key,d=item[1:]
        def work():
            data=resolve(self.project.read(key),d['route']);name=Path(key).name
            rows=text_rows(data,d['kind'],name)
            try:images=catalog(data,d['kind'],name);warning=''
            except ValueError as exc:images=[];warning='이미지 해석 미지원: '+str(exc)
            return data,rows,images,name,warning
        def done(value):
            self.data,self.rows,self.images,self.filename,warning=value;self.doc=(key,d);self.row_key=None
            self.title.set(d['name']);self.text.delete('1.0','end');self.text.edit_modified(False);self.original.set('')
            self.search.set('');self.fill_rows();self.image_list['values']=[i['name'] for i in self.images]
            self.canvas.delete('all');self.image_status.set('')
            if self.images:self.image_list.current(0);self.show_image()
            self.status.set(warning or f'문자열 {len(self.rows)}개 · 이미지 {len(self.images)}개 · {len(self.data):,} 바이트')
        self.run(work,done)

    def fill_rows(self):
        self.table.delete(*self.table.get_children());needle=self.search.get().casefold()
        for index,r in enumerate(self.rows):
            text=r.get('text') or ''
            if needle not in text.casefold():continue
            context=f"행 {r.get('row_id','')} {r.get('column','')}" if 'column' in r else f"시간 {r['time_raw']}" if 'time_raw' in r else ''
            self.table.insert('','end',iid=str(index),values=(r['index'],context,text.replace('\n',' ↵ ')))

    def select_row(self,event=None):
        chosen=self.table.selection()
        if not chosen:return
        if not self.pending_row():return
        self.row_key=int(chosen[0]);r=self.rows[self.row_key]
        self.original.set('저장 문자열: '+(r.get('text') or ''))
        self.text.delete('1.0','end');self.text.insert('1.0',r.get('text') or '');self.text.edit_modified(False)

    def apply_row(self):
        if self.busy:return False
        if self.row_key is None or not self.doc:return True
        if not self.text.edit_modified():return True
        try:
            text=self.text.get('1.0','end-1c');text=''.join(self.mapping.get(c,c) for c in text)
            r=self.rows[self.row_key];key,d=self.doc
            edited=edit_text(self.data,d['kind'],self.filename,{r['index']:text})
            self.project.stage(key,d['route'],edited);self.data=edited
            self.rows=text_rows(edited,d['kind'],self.filename);self.text.edit_modified(False)
            image_index=self.image_list.current()
            try:self.images=catalog(edited,d['kind'],self.filename)
            except ValueError:self.images=[]
            self.image_list['values']=[entry['name'] for entry in self.images]
            if self.images:
                self.image_list.current(max(0,min(image_index,len(self.images)-1)));self.show_image()
            self.fill_rows();self.status.set(f'문장 반영 완료 · 미저장 항목 {len(self.project.pending)}개')
            return True
        except Exception as exc:self.error(exc);return False

    def load_mapping(self):
        path=filedialog.askopenfilename(title='한글 → 게임 문자 매핑 JSON',filetypes=[('JSON','*.json')])
        if not path:return
        try:
            mapping=json.loads(Path(path).read_text(encoding='utf-8'))
            if not isinstance(mapping,dict) or not all(isinstance(k,str) and len(k)==1 and isinstance(v,str) for k,v in mapping.items()):raise ValueError('한 글자 키와 대체 문자열 값으로 된 JSON 객체가 필요합니다.')
            self.mapping=mapping;self.status.set(f'문자 매핑 {len(mapping)}개를 불러왔습니다. 폰트 자체를 변경하는 기능은 아닙니다.')
        except Exception as exc:self.error(exc)

    def edit_layout(self):
        if self.busy or not self.pending_row():return
        if not self.doc or self.doc[1]['kind']!='brlyt' or self.row_key is None:
            messagebox.showinfo('텍스트 상자 속성','BRLYT 파일에서 문자열 행을 선택해 주세요.',parent=self.window);return
        from n3ds_layout import properties,update,LABELS
        index=self.rows[self.row_key]['index'];current=properties(self.data,index)
        dialog=tk.Toplevel(self.window);dialog.title('텍스트 상자 · '+current['pane']);dialog.transient(self.window)
        frame=ttk.Frame(dialog,padding=18);frame.pack(fill='both',expand=True);variables={}
        ttk.Label(frame,text=current['pane'],font=(self.japanese_font,12,'bold')).grid(row=0,column=0,columnspan=2,sticky='w',pady=(0,12))
        for row,(key,label) in enumerate(LABELS.items(),1):
            ttk.Label(frame,text=label).grid(row=row,column=0,sticky='w',pady=5,padx=(0,16))
            variables[key]=tk.StringVar(value=str(current[key]))
            ttk.Entry(frame,textvariable=variables[key],width=20).grid(row=row,column=1,sticky='ew',pady=5)
        ttk.Label(frame,text=f"현재 문자열: {current['used_bytes']//2} UTF-16 단위 (종료 문자 포함)\n플레이스홀더와 줄바꿈은 문자열 탭에서 수정합니다.\n속성 변경은 실제 게임에서 표시 결과를 확인해야 합니다.",wraplength=480).grid(row=8,column=0,columnspan=2,sticky='w',pady=12)
        def apply():
            try:
                values={key:int(var.get()) if key=='capacity_units' else float(var.get()) for key,var in variables.items()}
                edited=update(self.data,index,values);key,doc=self.doc
                self.project.stage(key,doc['route'],edited);self.data=edited
                self.rows=text_rows(edited,'brlyt',self.filename);self.fill_rows()
                self.status.set('텍스트 상자 속성을 반영했습니다. 수정본 저장으로 RomFS 변경 파일을 저장하세요.')
                dialog.destroy()
            except Exception as exc:messagebox.showerror('속성 반영 실패',str(exc),parent=dialog)
        ttk.Button(frame,text='반영',command=apply,style='Accent.TButton').grid(row=9,column=1,sticky='e')
        ttk.Button(frame,text='취소',command=dialog.destroy).grid(row=9,column=0,sticky='w')
        dialog.grab_set()

    def show_image(self):
        index=self.image_list.current()
        if index<0 or index>=len(self.images):return
        try:
            info=self.images[index];im=preview(self.data,info)
            im.thumbnail((760,480));self.photo=ImageTk.PhotoImage(im)
            self.canvas.delete('all');self.canvas.create_image(15,15,anchor='nw',image=self.photo)
            self.image_status.set(f"{info['width']} × {info['height']} · 3DS {info.get('format','font')} · 어두운 배경은 미리보기용입니다.")
        except Exception as exc:self.error(exc)

    def export_image(self):
        i=self.image_list.current()
        if i<0 or not self.images:return
        path=filedialog.asksaveasfilename(title='PNG 내보내기',defaultextension='.png',filetypes=[('PNG','*.png')])
        if path:
            try:preview(self.data,self.images[i]).save(path)
            except Exception as exc:self.error(exc)

    def import_image(self):
        i=self.image_list.current()
        if self.busy or i<0 or not self.doc or not self.pending_row():return
        path=filedialog.askopenfilename(title='PNG로 교체 (TPL 크기 변경 가능)',filetypes=[('PNG','*.png')])
        if not path:return
        data=self.data;info=self.images[i];allow_lossy=self.lossy.get();key,d=self.doc;name=self.filename
        def work():
            with Image.open(path) as im:edited=replace(data,info,im,allow_lossy)
            return edited,catalog(edited,d['kind'],name)
        def done(result):
            edited,images=result;self.project.stage(key,d['route'],edited);self.data=edited;self.images=images
            self.image_list['values']=[entry['name'] for entry in self.images];self.image_list.current(i);self.show_image()
            self.status.set('이미지 교체 반영 완료. 수정본 저장을 눌러 주세요.')
        self.status.set('이미지를 변환하고 재포장하는 중…')
        self.run(work,done)

    def edit_font(self):
        if self.busy or not self.doc or not self.pending_row():return
        key,d=self.doc
        if d['kind']!='brfna':
            messagebox.showinfo('폰트','BRFNA 폰트를 선택해 주세요.',parent=self.window);return
        from compare_fonts import read_font_data
        from n3ds_font import edit_metadata
        from xeno_formats import num
        try:
            meta,mapping,glyphs,widths,sheets=read_font_data(self.data[:num(self.data,8)])
            dialog=tk.Toplevel(self.window);dialog.title('BRFNA · 문자 코드와 글리프 폭');dialog.geometry('600x600')
            table=ttk.Treeview(dialog,columns=('code','glyph','width'),show='headings',selectmode='browse')
            for col,title in [('code','문자 · Unicode'),('glyph','글리프 번호'),('width','왼쪽 / 폭 / 전진 폭')]:table.heading(col,text=title)
            scroll=ttk.Scrollbar(dialog,orient='vertical',command=table.yview);scroll.pack(side='right',fill='y')
            table.configure(yscrollcommand=scroll.set);table.pack(fill='both',expand=True)
            for code,glyph in sorted(mapping.items()):
                values=list(widths[glyph*3:glyph*3+3]);values[0]=values[0]-256 if values[0]>127 else values[0]
                table.insert('','end',iid=str(code),values=(f'{chr(code)} · U+{code:04X}',glyph,str(values)))
            fields=[]
            for title in ('새 문자 코드 (16진수)','왼쪽 베어링','글리프 폭','전진 폭'):
                row=ttk.Frame(dialog);row.pack(fill='x');ttk.Label(row,text=title,width=25).pack(side='left')
                var=tk.StringVar();ttk.Entry(row,textvariable=var).pack(side='left',fill='x',expand=True);fields.append(var)
            def select(event=None):
                selection=table.selection()
                if not selection:return
                code=int(selection[0]);glyph=mapping[code];values=list(widths[glyph*3:glyph*3+3]);values[0]=values[0]-256 if values[0]>127 else values[0]
                for var,value in zip(fields,[f'{code:04X}',*values]):var.set(str(value))
            table.bind('<<TreeviewSelect>>',select)
            def apply():
                if not table.selection():return
                try:
                    code=int(table.selection()[0]);new_code=int(fields[0].get().removeprefix('U+'),16)
                    values=[int(var.get()) for var in fields[1:]]
                    edited=edit_metadata(self.data,{code:new_code},{mapping[code]:values})
                    self.project.stage(key,d['route'],edited);self.data=edited
                    self.images=catalog(edited,'brfna',self.filename)
                    self.status.set('폰트 문자표·폭 반영 완료. 수정본 저장을 눌러 주세요.');dialog.destroy()
                except Exception as exc:self.error(exc)
            ttk.Label(dialog,text='문자 코드 변경만으로 게임의 문자열 인코딩이 바뀌지는 않습니다.').pack(pady=5)
            ttk.Button(dialog,text='선택 글리프 반영',command=apply).pack(pady=8)
            dialog.transient(self.window);dialog.grab_set()
        except Exception as exc:self.error(exc)

    def save(self):
        if self.busy or not self.project or not self.pending_row():return
        if not self.project.pending:messagebox.showinfo('저장','수정한 내용이 없습니다.');return
        path=filedialog.asksaveasfilename(title='새 출력 폴더 이름 지정',initialdir=str(LAB),initialfile='n3ds-edited')
        if path:
            self.status.set('수정한 영역을 저장하는 중…')
            self.run(lambda:self.project.save(path),lambda p:self.status.set(f'저장 완료: {p} · 변경 파일과 fsize.dat를 함께 사용하세요.'))

    def close(self):
        if self.busy:messagebox.showinfo('작업 중','현재 작업이 끝난 뒤 닫아 주세요.');return
        if (self.text.edit_modified() or self.project and self.project.dirty) and not messagebox.askyesno('닫기','저장하지 않은 수정 내용이 있습니다. 닫을까요?'):return
        self.window.destroy()


if __name__=='__main__':
    root=tk.Tk();Editor(root);root.mainloop()
