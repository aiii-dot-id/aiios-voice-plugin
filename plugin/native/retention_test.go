package main

import (
	"context"
	"encoding/json"
	"errors"
	"testing"

	"github.com/aiii-dot-id/aii-plugin-sdk/pkg/aiiosdk"
)

func TestRegistryOperationsCarryTheOperatorsVoiceprintRule(t *testing.T) {
	for _, op := range []string{"speaker.buckets", "speaker.associate", "speaker.forget", "speaker.link"} {
		if !registryOperation(op) {
			t.Fatalf("%s reads the registry but is not given the rule", op)
		}
	}
	for _, op := range []string{"speaker.list", "speaker.enroll", "speaker.recovery_list", "recording.list"} {
		if registryOperation(op) {
			t.Fatalf("%s does not read the registry", op)
		}
	}
	c := newCarrier(nil, nil, func() {})
	args := aiiosdk.Object(`{"speaker_uuid":"12345678-1234-4234-8234-123456789abc","registry_revision":"3"}`)
	reply := func(values string) func(context.Context) (aiiosdk.Object, error) {
		return func(context.Context) (aiiosdk.Object, error) {
			return aiiosdk.Object(`{"status":"succeeded","operation_result":{"values":` + values + `}}`), nil
		}
	}
	got := c.retentionFor(args, reply(`{"tts_voice":"marius","anonymous_profiles":"off","anonymous_expiry_days":7}`))
	var fields map[string]json.RawMessage
	if err := json.Unmarshal(got, &fields); err != nil {
		t.Fatal(err)
	}
	if string(fields[retentionArgument]) != `{"anonymous_expiry_days":7,"anonymous_profiles":"off"}` ||
		string(fields["registry_revision"]) != `"3"` || len(fields) != 3 {
		t.Fatalf("the rule was not handed on alone beside the caller's arguments: %s", got)
	}

	if got := c.retentionFor(args, reply(`{"tts_voice":"marius"}`)); !jsonEqual(t, got, `{"speaker_uuid":"12345678-1234-4234-8234-123456789abc","registry_revision":"3","_carrier_settings":{}}`) {
		t.Fatalf("unsaved settings were invented: %s", got)
	}

	failed := []func(context.Context) (aiiosdk.Object, error){
		func(context.Context) (aiiosdk.Object, error) { return nil, errors.New("no answer") },
		func(context.Context) (aiiosdk.Object, error) { return aiiosdk.Object(`{"status":"failed"}`), nil },
		reply(`[]`),
	}
	c.retentionFor(args, reply(`{"anonymous_expiry_days":9}`))
	for _, get := range failed {
		if got := c.retentionFor(args, get); !jsonEqual(t, got, `{"speaker_uuid":"12345678-1234-4234-8234-123456789abc","registry_revision":"3","_carrier_settings":{"anonymous_expiry_days":9}}`) {
			t.Fatalf("a failed read did not fall back to the rule last read: %s", got)
		}
	}

	c.keepRule(json.RawMessage(`{"anonymous_profiles":"off","turn_pause_ms":900}`))
	if got := c.retentionFor(args, failed[0]); !jsonEqual(t, got, `{"speaker_uuid":"12345678-1234-4234-8234-123456789abc","registry_revision":"3","_carrier_settings":{"anonymous_profiles":"off"}}`) {
		t.Fatalf("a session's read did not become the rule last read: %s", got)
	}

	fresh := newCarrier(nil, nil, func() {})
	for _, get := range failed {
		if got := fresh.retentionFor(args, get); string(got) != string(args) {
			t.Fatalf("a failed settings read changed the operation: %s", got)
		}
	}
}

func TestTheRulesReadEndsInsideTheHostsAllowance(t *testing.T) {
	if got := defaultLimits.retentionRead(); got != defaultLimits.HostRead {
		t.Fatalf("with the default table the read is given %s, not a read's time", got)
	}
	for _, table := range []*profileLimits{nil,
		{HostReadMS: ms(1500), HostWriteMS: ms(1500), StorageWaitMS: ms(2000)},
		{HostReadMS: ms(5000), HostWriteMS: ms(13000), StorageWaitMS: ms(13000)},
		{HostReadMS: ms(5000), HostWriteMS: ms(13000), StorageWaitMS: ms(14666)},
		{ControlMS: ms(13000)},
	} {
		l, err := limitsFrom(table)
		if err != nil {
			t.Fatal(err)
		}
		read := l.retentionRead()
		if read > l.HostRead || (read > 0 && read+l.storageOperation()+hostAnswerMargin > hostInvokeAllowance) {
			t.Fatalf("%+v: a read of %s and a storage control of %s pass the host's %s", table, read, l.storageOperation(), hostInvokeAllowance)
		}
	}
	edge, err := limitsFrom(&profileLimits{HostReadMS: ms(5000), HostWriteMS: ms(13000), StorageWaitMS: ms(14666)})
	if err != nil || edge.retentionRead() != hostInvokeAllowance-hostAnswerMargin-edge.storageOperation() || edge.retentionRead() >= edge.HostRead {
		t.Fatalf("at the edge of the host's allowance the read was given %s, not what is left (%v)", edge.retentionRead(), err)
	}
}

func TestACallerCannotSupplyTheCarriersSettings(t *testing.T) {
	for _, op := range []string{"speaker.buckets", "speaker.forget"} {
		args := hostEnrollmentArgs(op)
		if op == "speaker.forget" {
			args["speaker_uuid"], args["registry_revision"] = "12345678-1234-4234-8234-123456789abc", "1"
		}
		if err := validateEnrollment(op, enrollmentObject(args)); err != nil {
			t.Fatalf("%s fixture refused: %v", op, err)
		}
		args[retentionArgument] = map[string]any{"anonymous_expiry_days": 3650}
		if validateEnrollment(op, enrollmentObject(args)) == nil {
			t.Fatalf("%s accepted a caller's %s", op, retentionArgument)
		}
	}
}

func jsonEqual(t *testing.T, got aiiosdk.Object, want string) bool {
	t.Helper()
	var a, b any
	if json.Unmarshal(got, &a) != nil || json.Unmarshal([]byte(want), &b) != nil {
		return false
	}
	x, _ := json.Marshal(a)
	y, _ := json.Marshal(b)
	return string(x) == string(y)
}

func TestEveryOperationThatExpiresVoiceprintsIsDeclaredAWrite(t *testing.T) {
	seen := 0
	for _, d := range declaredPlugin().Descriptors() {
		switch {
		case registryOperation(d.ID):
			seen++
			if d.Effects != aiiosdk.EffectsWriteLocal {
				t.Errorf("%s applies the expiry but is declared %s", d.ID, d.Effects)
			}
			if d.OperatorConfirms != (d.ID != "speaker.buckets") {
				t.Errorf("%s operator_confirms is %v", d.ID, d.OperatorConfirms)
			}
		case d.ID == "speaker.list":
			if d.Effects != aiiosdk.EffectsReadInternal || d.OperatorConfirms {
				t.Errorf("speaker.list is %s, confirms %v; a read that asks nothing expected", d.Effects, d.OperatorConfirms)
			}
		}
	}
	if seen != 4 {
		t.Fatalf("%d registry operations declared, 4 expected", seen)
	}
}
