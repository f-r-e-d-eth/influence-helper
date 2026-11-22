#!/usr/bin/env python3
"""Adalaris v0.3 — streaming reconstruction with recovery archives."""
import argparse
import sys
import threading
import time
from pathlib import Path
from manifest import make_manifest, read, validate, STEP
from coordinator import (build, automatic_args, find_paused, background_files, last_activity, WORLDS, BACKGROUNDS, SETTINGS)
from settings import load_settings, next_check

def gui():
    from PySide6.QtCore import Qt, QThread, Signal, QSettings, QTimer
    from PySide6.QtGui import QPainter, QPixmap, QColor, QLinearGradient
    from PySide6.QtWidgets import (QApplication,QWidget,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,
        QProgressBar,QPlainTextEdit,QMessageBox,QFrame,QCheckBox,QGridLayout,QSlider)

    class Worker(QThread):
        message=Signal(str); progress=Signal(int,int); result=Signal(object); failed=Signal(str); stats=Signal(object); download=Signal(object)
        def __init__(self,args): super().__init__(); self.args=args; self.pause=threading.Event()
        def run(self):
            try: self.result.emit(build(*self.args[:6],self.message.emit,self.progress.emit,pause=self.pause,resume=self.args[6],stats=self.stats.emit,download_stats=self.download.emit))
            except Exception as exc: self.failed.emit(str(exc))

    class Window(QWidget):
        def __init__(self):
            super().__init__(); self.setWindowTitle('Adalaris · Influence World Builder v0.3'); self.resize(1120,900)
            self.settings=QSettings('InfluenceHelper','WorldBuilder'); self.worker=None
            self.image=QPixmap(); self.background_index=None; self.opacity=max(0,min(100,int(self.settings.value('opacity',88))))
            self.set_background(0)
            outer=QVBoxLayout(self); outer.setContentsMargins(28,28,28,28); outer.setSpacing(16)
            title=QLabel('ADALARIS  /  INFLUENCE WORLD BUILDER'); title.setStyleSheet('font-size: 28px; font-weight: 700; color: #e8f8ff'); outer.addWidget(title)
            sub=QLabel('V0.3  ·  RECONSTRUCT A VERIFIED LOCAL WORLD'); sub.setStyleSheet('color: #9ec7d8; font-size: 13px'); outer.addWidget(sub)
            self.panel=QFrame(); self.panel.setObjectName('panel'); outer.addWidget(self.panel); layout=QVBoxLayout(self.panel); layout.setSpacing(12); layout.setContentsMargins(20,20,20,20)
            self.storage = WORLDS
            self.skip=QCheckBox('Benchmark only — skip database fingerprints (UNVERIFIED archive)'); self.skip.setStyleSheet('color:#f7d38b'); layout.addWidget(self.skip)
            self.start=QPushButton('RUN'); self.start.setObjectName('build'); self.start.clicked.connect(self.action); layout.addWidget(self.start)
            self.bar=QProgressBar(); self.bar.setValue(0); layout.addWidget(self.bar)
            self.status=QLabel('Ready'); self.status.setWordWrap(True); layout.addWidget(self.status)
            self.download_label=QLabel('Download · waiting'); self.download_label.setWordWrap(True); layout.addWidget(self.download_label)
            self.download_bar=QProgressBar(); self.download_bar.setRange(0,100); self.download_bar.setValue(0); layout.addWidget(self.download_bar)
            self.dashboard=QLabel(); self.dashboard.setWordWrap(True); self.dashboard.setStyleSheet('font-size:15px; padding:12px; color:#d8f3f5'); layout.addWidget(self.dashboard)
            self.recovery_label=QLabel('Recovery archive · waiting'); self.recovery_label.setWordWrap(True); layout.addWidget(self.recovery_label)
            self.activity_title=QLabel('LAST COMPLETED DELTA · COLLECTION ACTIVITY'); self.activity_title.setStyleSheet('font-size:16px;font-weight:bold;color:#d8f3f5'); layout.addWidget(self.activity_title)
            charts=QHBoxLayout(); charts.setSpacing(20); layout.addLayout(charts)
            self.scan_chart=self.make_chart(charts,'Documents scanned · final fingerprint','#67cfbd')
            self.write_chart=self.make_chart(charts,'Write operations · applied delta','#e0ae69')
            self.log=QPlainTextEdit(); self.log.setReadOnly(True); self.log.setMaximumBlockCount(500); outer.addWidget(self.log,1)
            controls=QHBoxLayout(); controls.addWidget(QLabel('Panel opacity'))
            self.opacity_slider=QSlider(Qt.Horizontal); self.opacity_slider.setRange(0,100); self.opacity_slider.setValue(self.opacity)
            self.opacity_slider.setMaximumWidth(260); controls.addWidget(self.opacity_slider)
            self.opacity_value=QLabel(f'{self.opacity}%'); controls.addWidget(self.opacity_value); controls.addStretch(1); outer.addLayout(controls)
            self.opacity_slider.valueChanged.connect(self.change_opacity)
            self.phase_started=time.monotonic(); self.phase=''; self.timer=QTimer(self); self.timer.setInterval(1000); self.timer.timeout.connect(self.tick)
            self.style(); self.refresh_idle()
        def set_background(self, completed):
            if self.background_index == completed: return
            self.background_index=completed
            try:
                BACKGROUNDS.mkdir(parents=True,exist_ok=True)
                paths=background_files(BACKGROUNDS)
            except OSError:
                paths=[]
            for offset in range(len(paths)):
                pix=QPixmap(str(paths[(completed+offset)%len(paths)]))
                if not pix.isNull():
                    self.image=pix; self.update(); return
            self.image=QPixmap(); self.update()
        def change_opacity(self,value):
            self.opacity=value; self.settings.setValue('opacity',value); self.opacity_value.setText(f'{value}%'); self.style(); self.update()
        def style(self):
            self.setStyleSheet('QLabel{color:#e1edf5} QFrame#panel{background-color:rgba(12,25,42,'+str(self.opacity*255//100)+');border:1px solid #405568;border-radius:14px} QPlainTextEdit{background:rgba(17,31,50,'+str(self.opacity*255//100)+');color:#edf6ff;border:1px solid #496179;border-radius:6px;padding:8px} QFrame#activityChart{background:rgba(16,34,50,'+str(self.opacity*180//100)+');border-radius:8px} QPushButton{background:#233e56;color:#edf6ff;border:1px solid #52748b;border-radius:7px;padding:9px} QPushButton#build{background:#176b61;font-weight:bold;padding:14px} QPushButton:disabled{color:#8294a5;background:#253342} QProgressBar{color:white;background:#18283a;border-radius:5px;text-align:center} QProgressBar::chunk{background:#258c7e;border-radius:5px}')
        def paintEvent(self,event):
            painter=QPainter(self); g=QLinearGradient(0,0,self.width(),self.height()); g.setColorAt(0,QColor('#153c50')); g.setColorAt(1,QColor('#100f29')); painter.fillRect(self.rect(),g)
            if not self.image.isNull():
                scaled=self.image.scaled(self.size(),Qt.KeepAspectRatioByExpanding,Qt.SmoothTransformation); painter.drawPixmap((self.width()-scaled.width())//2,(self.height()-scaled.height())//2,scaled)
        def refresh_idle(self):
            try:
                settings=load_settings(SETTINGS)
                args=automatic_args(self.storage,not self.skip.isChecked())
                self.start.setEnabled(True)
                self.skip.setEnabled(args[6] is None)
                if args[6]:
                    record=read(args[6]); self.skip.setChecked(not args[5])
                    self.start.setText('RESUME')
                    self.status.setText('Paused build found · ready to resume')
                    last=record.get('applied_through')
                    self.show_stats(dict(stage='Paused',target=args[4],applied=last,
                        checkpoints=(last+1)//STEP if last is not None else 0,total_checkpoints=(args[4]+1)//STEP,
                        verification=args[5],timings=record.get('timings',{}),activity=last_activity(args[6].parent,last),
                        verified=record.get('verified_through'), recovery=record.get('recovery_archives',[]),
                        recovery_status=record.get('recovery_status','Waiting'),
                        next_verification=next_check(last,args[4],record['config'].get('settings',settings)) if args[5] else None))
                else:
                    self.start.setText('RUN')
                    if args[0] is None:
                        self.recovery_label.setText(f"Recovery interval: {settings['checkpoint']['interval_blocks']:,} blocks" if settings['checkpoint']['enabled'] else 'Recovery archives disabled')
                        self.status.setText('Ready · manifest will be fetched from configured HTTPS source'); return
                    entries=validate(read(args[0]))
                    self.status.setText(f'Ready · {len(entries)-1} available checkpoints')
                    self.show_stats(dict(stage='Ready',target=args[4],total_checkpoints=len(entries)-1,
                        checkpoints=0,input_bytes=sum(e['bytes'] for e in entries),verification=args[5],
                        next_verification=next_check(None,args[4],settings) if args[5] else None))
            except Exception as exc:
                self.start.setText('RUN'); self.start.setEnabled(True); self.skip.setEnabled(True)
                self.status.setText(str(exc))
        def make_chart(self, parent, title, color):
            panel=QFrame(); panel.setObjectName('activityChart')
            parent.addWidget(panel,1); column=QVBoxLayout(panel); column.setContentsMargins(10,8,10,8); column.setSpacing(4)
            heading=QLabel(title); heading.setStyleSheet('font-weight:bold;color:#c9e7ef'); column.addWidget(heading)
            note=QLabel('No completed delta yet'); note.setStyleSheet('color:#a7bdc8'); column.addWidget(note)
            grid=QGridLayout(); grid.setHorizontalSpacing(8); grid.setVerticalSpacing(3); grid.setColumnStretch(1,1); column.addLayout(grid)
            rows=[]
            for i in range(10):
                name=QLabel(); name.setTextFormat(Qt.PlainText); name.setStyleSheet('font-size:11px;color:#e2eef4')
                bar=QProgressBar(); bar.setRange(0,1000); bar.setTextVisible(False); bar.setFixedHeight(9)
                bar.setStyleSheet('QProgressBar{background:#253747;border:0;border-radius:4px} QProgressBar::chunk{background:'+color+';border-radius:4px}')
                value=QLabel(); value.setAlignment(Qt.AlignRight); value.setStyleSheet('font-size:11px;color:#e2eef4')
                for col,widget in enumerate((name,bar,value)): grid.addWidget(widget,i,col); widget.hide()
                rows.append((name,bar,value))
            column.addStretch(1)
            return note,rows
        def draw_chart(self, chart, values, empty):
            note,rows=chart
            note.setText(empty); note.setVisible(not values)
            maximum=values[0][1] if values else 1
            for i,(name,bar,value) in enumerate(rows):
                visible=i<len(values)
                for widget in (name,bar,value): widget.setVisible(visible)
                if visible:
                    label,count=values[i]
                    name.setText(label.removeprefix('Component_')); name.setToolTip(label)
                    bar.setValue(max(1,round(1000*count/maximum)))
                    value.setText(f'{count:,}')
        def show_activity(self, activity):
            if not activity:
                self.activity_title.setText('LAST COMPLETED DELTA · COLLECTION ACTIVITY')
                self.draw_chart(self.scan_chart,[],'No completed delta yet')
                self.draw_chart(self.write_chart,[],'No completed delta yet')
                return
            self.activity_title.setText(f'LAST COMPLETED DELTA · BLOCK {activity["block"]:,} · TOP 10')
            self.draw_chart(self.scan_chart,activity['scanned'],'No documents scanned' if activity['scans_available'] else 'Fingerprint scans skipped')
            self.draw_chart(self.write_chart,activity['writes'],'No write operations')
        def show_download(self,data):
            if 'total' in data:
                self.download_bar.setValue(int(100*data['received']/max(1,data['total'])))
            speed=data.get('speed')
            rate=f' · {speed/1024**2:.1f} MiB/s' if speed else ''
            disk=f" · {data['free_bytes']/1024**3:.1f} GiB free" if 'free_bytes' in data else ''
            self.download_label.setText(f"Download · {data.get('status','')} · {data.get('file','')}{rate} · {data.get('buffered',0)} buffered{disk}")
        def show_stats(self, data):
            self.set_background(data.get('checkpoints',0))
            def number(n): return f'{n:,}' if type(n) is int and n>=0 else '—'
            def size(n): return f'{n/1024**2:,.1f} MiB' if type(n) is int else '—'
            def duration(n):
                if not isinstance(n,(int,float)): return '—'
                return f'{int(n)//60} min {int(n)%60:02d} s' if n>=60 else f'{n:.1f} s'
            e=data.get('current',{}); t=data.get('timings',{})
            rows=[
                f"Target block: {number(data.get('target'))}     ·     Completed: {number(data.get('applied'))}",
                f"Deltas: {data.get('checkpoints',0)} / {data.get('total_checkpoints',0)}     ·     {'Verified mode' if data.get('verification') else 'Benchmark mode'}",
                f"Last verified: {number(data.get('verified'))}     ·     Next full check: {number(data.get('next_verification'))}",
                f"Current file: {e.get('filename','—')}     ·     {size(e.get('bytes'))}",
                f"Expected target documents: {number(e.get('target_documents'))}     ·     Planned operations: {number(e.get('completed_operations'))}",
                f"Completed documents (manifest): {number(data.get('documents'))}     ·     Completed operations (manifest): {number(data.get('operations_done'))}",
                f"Fingerprint time: {duration(t.get('fingerprints_seconds'))}     ·     Write time: {duration(t.get('bulk_writes_seconds'))}",
                f"Input package size: {size(data.get('input_bytes'))}     ·     Export: {size(data.get('archive_bytes'))}",
            ]
            self.dashboard.setText('\n'.join(rows))
            self.dashboard.setTextFormat(Qt.PlainText)
            recovery=data.get('recovery',[]); latest=recovery[-1] if recovery else {}
            status=data.get('recovery_status','Waiting')
            disk=data.get('free_bytes'); free=f'{disk/1024**3:,.1f} GiB' if type(disk) is int else '—'
            self.recovery_label.setText(f"Recovery · {status} · Last saved: {number(latest.get('block'))} · {size(latest.get('bytes'))} · Disk free: {free}")
            self.recovery_label.setStyleSheet('color:#67cfbd' if recovery or status=='Final archive saved' else 'color:#f7d38b')
            self.show_activity(data.get('activity'))
        def tick(self):
            if self.worker and self.worker.isRunning():
                elapsed=int(time.monotonic()-self.phase_started)
                self.status.setText(f'{self.phase} · elapsed {elapsed//60} min {elapsed%60:02d} s')
        def message(self, msg):
            self.log.appendPlainText(msg)
            self.phase=msg; self.phase_started=time.monotonic(); self.status.setText(msg)
        def ready(self, result):
            self.timer.stop()
            if result['status']=='paused':
                self.status.setText('Paused safely · you can close Adalaris'); return
            self.bar.setValue(self.bar.maximum())
            self.status.setText(('Verified archive ready: ' if result['fingerprints_verified'] else 'UNVERIFIED benchmark archive: ')+result['archive'])
        def failed(self, error):
            self.timer.stop(); self.status.setText('Stopped: '+error); self.log.appendPlainText('ERROR: '+error)
        def action(self):
            if self.worker and self.worker.isRunning():
                self.worker.pause.set(); self.start.setText('PAUSING…'); self.start.setEnabled(False)
                self.message('Pause requested · waiting for the next safe checkpoint')
                return
            try:
                args=automatic_args(self.storage,not self.skip.isChecked())
                self.launch(args)
            except Exception as exc: self.failed(str(exc))
        def finished(self):
            self.start.setEnabled(True)
            paused=find_paused(self.storage)
            self.start.setText('RESUME' if paused else 'RUN')
            self.skip.setEnabled(not paused)
            if paused: self.skip.setChecked(not paused[1]['config']['verify_fingerprints'])
        def launch(self,args):
            self.background_index=None
            self.start.setText('PAUSE'); self.start.setEnabled(True); self.skip.setEnabled(False)
            self.log.clear(); self.bar.setValue(0); self.status.setText('Resuming build…' if args[6] else 'Building a new isolated database…')
            self.worker=Worker(args); self.worker.message.connect(self.message); self.worker.progress.connect(lambda n,t:(self.bar.setMaximum(t),self.bar.setValue(n))); self.worker.stats.connect(self.show_stats); self.worker.download.connect(self.show_download); self.worker.result.connect(self.ready); self.worker.failed.connect(self.failed); self.worker.finished.connect(self.finished); self.phase='Preparing build'; self.phase_started=time.monotonic(); self.timer.start(); self.worker.start()
        def closeEvent(self,event):
            if self.worker and self.worker.isRunning(): QMessageBox.information(self,'Build running','Use PAUSE and wait for confirmation before closing.'); event.ignore()
            else: event.accept()

    app=QApplication(sys.argv); window=Window(); window.show(); return app.exec()


if __name__ == '__main__':
    parser=argparse.ArgumentParser(); sub=parser.add_subparsers(dest='command')
    m=sub.add_parser('manifest'); m.add_argument('--data',type=Path,required=True); m.add_argument('--end',type=int,required=True); m.add_argument('--output',type=Path,required=True); m.add_argument('--cids',type=Path)
    args=parser.parse_args()
    if args.command=='manifest': make_manifest(args.data,args.end,args.output,args.cids)
    else: sys.exit(gui())
