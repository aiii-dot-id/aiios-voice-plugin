#include "c_api.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
extern aii_voice_models* aii_test_models(void);
extern unsigned aii_test_cancel_count(void);
extern void aii_test_yield(void);
#define REQUIRE(x) do { if(!(x)) { fprintf(stderr,"line %d: %s\n",__LINE__,#x);abort(); } } while(0)
static aii_voice_error error;
#define OK(x) do { aii_voice_result result=(x); if(result!=AII_VOICE_OK) { fprintf(stderr,"line %d: result=%d %s\n",__LINE__,result,error.message);abort(); } } while(0)
static aii_voice_snapshot snapshot(aii_voice_session* s) {
  aii_voice_snapshot v;OK(aii_voice_status(s,&v,&error));REQUIRE(!*v.error);return v;
}
static void retired_synthesis(aii_voice_session* s) {
  unsigned i;for(i=0;i<2000 && snapshot(s).synthesizing;++i)aii_test_yield();REQUIRE(i<2000);
}
int main(void) {
  aii_voice_models* m=aii_test_models();aii_voice_session* s=NULL;aii_voice_session* second=NULL;
  aii_voice_readiness ready;
  {
    char execution[256]="sentinel";size_t needed=99;
    REQUIRE(aii_voice_models_execution(NULL,execution,sizeof execution,&needed,&error)==AII_VOICE_INVALID);
    REQUIRE(needed==0 && execution[0]==0);
    REQUIRE(aii_voice_models_execution(m,NULL,0,&needed,&error)==AII_VOICE_CAPACITY && needed>1 && needed<sizeof execution);
    REQUIRE(aii_voice_models_execution(m,execution,needed-1,&needed,&error)==AII_VOICE_CAPACITY && execution[0]==0);
    OK(aii_voice_models_execution(m,execution,needed,&needed,&error));
    REQUIRE(needed==strlen(execution)+1 && strstr(execution,"\"hardware_execution_verified\":false"));
    REQUIRE(strstr(execution,"unspecified model owner")); /* no invented accelerator */
    REQUIRE(aii_voice_models_execution(m,NULL,1,&needed,&error)==AII_VOICE_INVALID && needed==0);
  }
  REQUIRE(aii_voice_models_warm(NULL,&ready,&error)==AII_VOICE_INVALID);
  REQUIRE(aii_voice_models_warm(m,NULL,&error)==AII_VOICE_INVALID);
  OK(aii_voice_models_warm(m,&ready,&error));REQUIRE(ready.models_loaded==4 && ready.probe_ms==1 && !strcmp(ready.accelerator,"cpu"));
  aii_voice_settings bad={0,3000,.5f};
  REQUIRE(aii_voice_open(m,&bad,&s,&error)==AII_VOICE_INVALID && !s && *error.message);
  aii_voice_speech_settings unsupported={"alba","fr","en",.3f,20260908};
  REQUIRE(aii_voice_open_configured(m,NULL,&unsupported,&s,&error)==AII_VOICE_INVALID && !s);
  unsupported.tts_language="en";unsupported.voice=NULL;
  REQUIRE(aii_voice_open_configured(m,NULL,&unsupported,&s,&error)==AII_VOICE_INVALID && !s);
  unsupported.voice="marius";
  REQUIRE(aii_voice_open_configured(m,NULL,&unsupported,&s,&error)==AII_VOICE_INVALID && !s); /* fake backend cannot silently accept */
  OK(aii_voice_open(m,NULL,&s,&error));
  {
    const uint64_t finals[3]={11,12,13};char candidate[128];size_t needed=0;
    REQUIRE(aii_voice_enroll_selected(s,"fixture",7,"person","Chosen",finals,3,NULL,0,&needed,&error)==AII_VOICE_CAPACITY && needed==28);
    OK(aii_voice_enroll_selected(s,"fixture",7,"person","Chosen",finals,3,candidate,sizeof candidate,&needed,&error));
    REQUIRE(!strcmp(candidate,"canonical candidate fixture"));
    {uint64_t available[3]={0};size_t count=0;
      REQUIRE(aii_voice_enrollment_finals(s,available,2,&count,&error)==AII_VOICE_CAPACITY && count==3);
      OK(aii_voice_enrollment_finals(s,available,3,&count,&error));
      REQUIRE(count==3&&available[0]==11&&available[2]==13);
    }
    REQUIRE(aii_voice_enroll_selected(s,"fixture",7,"person","Chosen",finals,0,candidate,sizeof candidate,&needed,&error)==AII_VOICE_INVALID);
  }
  REQUIRE(aii_voice_models_warm(m,&ready,&error)==AII_VOICE_BUSY);
  REQUIRE(aii_voice_open(m,NULL,&second,&error)==AII_VOICE_BUSY && !second);
  REQUIRE(aii_voice_models_release(&m,&error)==AII_VOICE_BUSY && m);
  REQUIRE(aii_voice_release(&s,&error)==AII_VOICE_BUSY && s);
  REQUIRE(aii_voice_synthesize(s,1,"bad\0text",8,&error)==AII_VOICE_INVALID);
  REQUIRE(aii_voice_close(s,2,&error)==AII_VOICE_INVALID);
  REQUIRE(aii_voice_wait(s,30001,&error)==AII_VOICE_INVALID);
  REQUIRE(aii_voice_status(NULL,NULL,&error)==AII_VOICE_INVALID);
  OK(aii_voice_synthesize(s,1,"A blocked answer.",17,&error));
  aii_voice_event event;size_t n=0;char text[256];
  REQUIRE(aii_voice_next_event(s,&event,NULL,0,&n,&error)==AII_VOICE_CAPACITY && n==1);
  OK(aii_voice_next_event(s,&event,text,sizeof text,&n,&error));
  REQUIRE(event.sequence==1 && !strcmp(event.kind,"synthesis_start"));
  OK(aii_voice_stop_playback(s,1,&error));
  REQUIRE(aii_test_cancel_count()==0 && snapshot(s).synthesizing);
  OK(aii_voice_playback(s,1,0,1,1,&error));
  OK(aii_voice_cancel_synthesis(s,1,&error));retired_synthesis(s);
  aii_voice_generation g;OK(aii_voice_generation_status(s,1,&g,&error));
  REQUIRE(g.fenced && g.cancelled && g.retired && g.receipt && g.stopped && aii_test_cancel_count()>0);
  aii_voice_audio audio;
  OK(aii_voice_next_audio(s,&audio,NULL,0,&n,&error));REQUIRE(audio.end && audio.generation==1 && n==0);
  OK(aii_voice_synthesize(s,2,"Recovery.",9,&error));retired_synthesis(s);
  REQUIRE(aii_voice_next_audio(s,&audio,NULL,0,&n,&error)==AII_VOICE_CAPACITY && n==960);
  OK(aii_voice_generation_status(s,2,&g,&error));REQUIRE(g.delivered==0);
  float pcm[1025];OK(aii_voice_next_audio(s,&audio,pcm,1025,&n,&error));
  REQUIRE(n==960 && !audio.end && pcm[0]==.25f);
  OK(aii_voice_next_audio(s,&audio,NULL,0,&n,&error));REQUIRE(audio.end && audio.start==960);
  OK(aii_voice_playback(s,2,960,1,0,&error));
  OK(aii_voice_finish_input(s,1025,&error));OK(aii_voice_close(s,0,&error));
  { const uint64_t f[1]={11};char out[128];size_t n=0;
    REQUIRE(aii_voice_enroll_selected(s,"fixture",7,"person","Chosen",f,1,out,sizeof out,&n,&error)==AII_VOICE_INVALID);
    REQUIRE(aii_voice_enrollment_finals(s,NULL,0,&n,&error)==AII_VOICE_INVALID);
  }
  OK(aii_voice_finish_input(s,1025,&error));
  REQUIRE(aii_voice_finish_input(s,1026,&error)==AII_VOICE_INVALID);
  memset(pcm,0,sizeof pcm);OK(aii_voice_feed(s,0,pcm,1025,&error));
  OK(aii_voice_wait(s,2000,&error));
  aii_voice_snapshot v=snapshot(s);REQUIRE(v.retired && !v.aborted && v.received==1025 && v.recognized==1025 && v.cutoff_set && v.input_finished);
  unsigned final=0,finished=0;uint64_t sequence=1;
  for(;;) {
    aii_voice_result r=aii_voice_next_event(s,&event,text,sizeof text,&n,&error);
    if(r==AII_VOICE_AGAIN)break;REQUIRE(r==AII_VOICE_OK);REQUIRE(event.sequence==++sequence);
    if(!strcmp(event.kind,"transcript_final")) { ++final;REQUIRE(!strcmp(text,"opening words retained")); }
    if(!strcmp(event.kind,"input_finished")) { ++finished;REQUIRE(final==1 && event.end==1025); }
  }
  REQUIRE(final==1 && finished==1);
  OK(aii_voice_release(&s,&error));REQUIRE(!s);
  OK(aii_voice_open(m,NULL,&s,&error));OK(aii_voice_close(s,1,&error));OK(aii_voice_wait(s,2000,&error));
  REQUIRE(snapshot(s).aborted);OK(aii_voice_release(&s,&error));OK(aii_voice_models_release(&m,&error));REQUIRE(!m);
  puts("C caller: bounded custody, independent controls, future Finish, exact transcript, retirement and model lease PASS");
  return 0;
}
