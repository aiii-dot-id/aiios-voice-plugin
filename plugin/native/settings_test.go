package main

import (
	"bufio"
	"context"
	"encoding/json"
	"io"
	"testing"
	"time"

	"github.com/aiii-dot-id/aii-plugin-sdk/pkg/aiiosdk"
)

func TestSettingsEnvelopeRequiresExplicitObjectSuccess(t *testing.T) {
	for _, raw := range []string{
		`{}`, `null`, `{"status":"failed","operation_result":{"values":{}}}`,
		`{"status":"succeeded","success":false,"operation_result":{"values":{}}}`,
		`{"status":"succeeded","operation_result":{"values":null}}`,
		`{"status":"succeeded","operation_result":{"values":[]}}`,
		`{"status":"succeeded","values":{"tts_language":"german"}}`,
	} {
		if _, err := settingsValues([]byte(raw)); err == nil {
			t.Fatalf("accepted malformed/refused settings %s", raw)
		}
	}
	values, err := settingsValues([]byte(`{"status":"succeeded","operation_result":{"values":{"tts_language":"german"}}}`))
	if err != nil || string(values) != `{"tts_language":"german"}` {
		t.Fatalf("lost values: %s %v", values, err)
	}
}

func TestSettingsWaitDoesNotHoldControlOrReplyReader(t *testing.T) {
	c, requests, replies := privateFixture(t)
	entered, release, done := make(chan struct{}), make(chan struct{}), make(chan struct{})
	go func() {
		defer close(done)
		c.readSettings(settingsQuery{1, "first"}, func(ctx context.Context) (aiiosdk.Object, error) {
			close(entered)
			select {
			case <-release:
				return aiiosdk.Object(`{"status":"succeeded","operation_result":{"values":{"stt_language":"de-DE"}}}`), nil
			case <-ctx.Done():
				return nil, ctx.Err()
			}
		})
	}()
	t.Cleanup(func() { c.fail(io.EOF); <-done })
	<-entered
	stop := pendingResult(t, c, "stop_playback")
	scan := bufio.NewScanner(requests)
	if !scan.Scan() {
		t.Fatal("control was not delivered")
	}
	var control privateRequest
	if err := json.Unmarshal(scan.Bytes(), &control); err != nil || control.Operation != "stop_playback" {
		t.Fatalf("unexpected control %s", scan.Bytes())
	}
	if _, err := io.WriteString(replies, `{"id":1,"result":{"fenced":true}}`+"\n"); err != nil {
		t.Fatal(err)
	}
	if answer := awaitResult(t, stop); answer.Err != nil {
		t.Fatal(answer.Err)
	}
	close(release)
	if !scan.Scan() {
		t.Fatal("no settings outcome")
	}
	var settings privateRequest
	if err := json.Unmarshal(scan.Bytes(), &settings); err != nil || settings.Settings == nil || settings.Settings.SessionID != "first" || string(settings.Settings.Values) != `{"stt_language":"de-DE"}` {
		t.Fatalf("lost/crossed settings outcome %s", scan.Bytes())
	}
	<-done
}

func TestSettingsReadIsCancelledWhenCarrierEnds(t *testing.T) {
	c, _, _ := privateFixture(t)
	entered, done := make(chan struct{}), make(chan struct{})
	go func() {
		defer close(done)
		c.readSettings(settingsQuery{1, "s"}, func(ctx context.Context) (aiiosdk.Object, error) {
			close(entered)
			<-ctx.Done()
			return nil, ctx.Err()
		})
	}()
	<-entered
	c.fail(io.EOF)
	select {
	case <-done:
	case <-time.After(time.Second):
		t.Fatal("settings wait survived carrier retirement")
	}
}
