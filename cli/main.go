package main

import (
	"bytes"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"
)

var (
	version   = "main"
	repoOwner = "TGJLS/Testing-Kit"
)

const apiURL = "http://localhost:1234"

func main() {
	if len(os.Args) < 2 {
		usage()
		os.Exit(1)
	}
	switch os.Args[1] {
	case "install":
		cmdInstall()
	case "up":
		cmdCompose("up", "-d")
	case "down":
		cmdCompose("down")
	case "reset":
		cmdCompose("down", "-v")
	case "run-tests":
		cmdRunTests()
	case "add-extender":
		cmdAddExtender(os.Args[2:])
	default:
		fmt.Fprintf(os.Stderr, "unknown command: %s\n", os.Args[1])
		usage()
		os.Exit(1)
	}
}

func usage() {
	fmt.Fprintln(os.Stderr, "Usage: testing-kit-cli <command>")
	fmt.Fprintln(os.Stderr, "Commands: install, up, down, reset, run-tests, add-extender")
}

func die(msg string) {
	fmt.Fprintf(os.Stderr, "✗ %s\n", msg)
	os.Exit(1)
}

func cmdCompose(args ...string) {
	cmd := exec.Command("docker", append([]string{"compose"}, args...)...)
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	if err := cmd.Run(); err != nil {
		fmt.Fprintf(os.Stderr, "docker compose failed: %v\n", err)
		os.Exit(1)
	}
}

func cmdRunTests() {
	resp, err := http.Post(apiURL+"/v1/run-tests", "application/json", nil)
	if err != nil {
		die(fmt.Sprintf("Error calling API: %v", err))
	}
	defer resp.Body.Close()

	body, _ := io.ReadAll(resp.Body)

	if resp.StatusCode != http.StatusOK {
		die(fmt.Sprintf("API error %d: %s", resp.StatusCode, strings.TrimSpace(string(body))))
	}

	var result struct {
		Passed         int `json:"passed"`
		Failed         int `json:"failed"`
		TimedOut       int `json:"timed_out"`
		DispatchFailed int `json:"dispatch_failed"`
		Xfail          int `json:"xfail"`
		Results        []struct {
			Cmdline string `json:"cmdline"`
			Status  string `json:"status"`
			Output  string `json:"output"`
			ErrMsg  string `json:"err_msg"`
		} `json:"results"`
	}

	if err := json.Unmarshal(body, &result); err != nil {
		die(fmt.Sprintf("Error parsing API response: %v", err))
	}

	for _, r := range result.Results {
		icon := "✓"
		if r.Status == "failed" || r.Status == "timed-out" || r.Status == "dispatch-failed" {
			icon = "✗"
		} else if r.Status == "xfail" {
			icon = "⚠"
		}
		fmt.Printf("  %s [%s] %s\n", icon, r.Status, r.Cmdline)
		if r.Output != "" && r.Status != "passed" {
			fmt.Printf("    %s\n", r.Output)
		}
		if r.ErrMsg != "" {
			fmt.Printf("    err: %s\n", r.ErrMsg)
		}
	}

	fmt.Printf("\n%d passed, %d failed, %d timed out, %d dispatch-failed, %d xfail\n",
		result.Passed, result.Failed, result.TimedOut, result.DispatchFailed, result.Xfail)

	if result.Failed+result.TimedOut+result.DispatchFailed > 0 {
		os.Exit(1)
	}
}

func cmdInstall() {
	checkSoftware()
	downloadFiles()
	checkBtrfs()
	generateSSHKey()
	downloadOpenSSH()
	cmdCompose("up", "-d")
}

func checkSoftware() {
	fmt.Println("Checking prerequisites...")
	if _, err := exec.LookPath("docker"); err != nil {
		die("docker not found in PATH — install Docker first")
	}
	cmd := exec.Command("docker", "compose", "version")
	cmd.Stdout = io.Discard
	cmd.Stderr = io.Discard
	if err := cmd.Run(); err != nil {
		die("docker compose (v2) not available — install docker-compose-plugin or Docker Desktop")
	}
	fmt.Println("✓ docker and docker compose found")
}

func rawURL(path string) string {
	return fmt.Sprintf("https://raw.githubusercontent.com/%s/%s/%s", repoOwner, version, path)
}

func downloadToFile(url, dest string) error {
	if err := os.MkdirAll(filepath.Dir(dest), 0o755); err != nil {
		return err
	}
	resp, err := http.Get(url)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("HTTP %d fetching %s", resp.StatusCode, url)
	}
	data, err := io.ReadAll(resp.Body)
	if err != nil {
		return err
	}
	return os.WriteFile(dest, data, 0o644)
}

func downloadFiles() {
	fmt.Println("Checking KVM availability...")
	composeSource := "docker-compose.yml"
	if _, err := os.Stat("/dev/kvm"); err == nil {
		fmt.Println("✓ /dev/kvm found — using KVM-enabled compose file")
		composeSource = "docker-compose.kvm.yml"
	} else {
		fmt.Println("⚠  /dev/kvm not found — using standard compose file (software emulation)")
	}

	fmt.Printf("Downloading docker-compose.yml from %s @ %s...\n", repoOwner, version)
	if err := downloadToFile(rawURL(composeSource), "docker-compose.yml"); err != nil {
		die(fmt.Sprintf("Failed to download compose file: %v", err))
	}
	fmt.Println("✓ docker-compose.yml downloaded")

	for _, f := range []string{"config/config.yaml", ".github/cicd/tasks.yaml"} {
		if _, err := os.Stat(f); err == nil {
			fmt.Printf("⚠  %s already exists — skipping\n", f)
			continue
		}
		if err := downloadToFile(rawURL(f), f); err != nil {
			die(fmt.Sprintf("Failed to download %s: %v", f, err))
		}
		fmt.Printf("✓ %s downloaded\n", f)
	}
}

func checkBtrfs() {
	fmt.Println("Checking filesystem...")
	out, err := exec.Command("docker", "info", "--format", "{{.DockerRootDir}}").Output()
	if err != nil {
		fmt.Println("⚠  Could not determine Docker root — skipping btrfs check")
		return
	}
	dockerRoot := strings.TrimSpace(string(out))

	fsOut, err := exec.Command("stat", "-f", "-c", "%T", dockerRoot).Output()
	if err != nil || strings.TrimSpace(string(fsOut)) != "btrfs" {
		fmt.Println("✓ Not btrfs — skipping copy-on-write adjustment")
		return
	}

	fmt.Println("btrfs detected — checking copy-on-write on windows-data volume...")
	exec.Command("docker", "volume", "create", "testing-kit_windows-data").Run() //nolint:errcheck

	attrOut, _ := exec.Command("docker", "run", "--rm", "--cap-add", "LINUX_IMMUTABLE",
		"-v", "testing-kit_windows-data:/data", "alpine", "sh", "-c",
		"apk add --no-cache e2fsprogs-extra >/dev/null 2>&1; lsattr -d /data").Output()

	if strings.Contains(string(attrOut), "C") {
		fmt.Println("✓ Copy-on-write already disabled on testing-kit_windows-data")
		return
	}

	exec.Command("docker", "run", "--rm", "--cap-add", "LINUX_IMMUTABLE", //nolint:errcheck
		"-v", "testing-kit_windows-data:/data", "alpine", "sh", "-c",
		"apk add --no-cache e2fsprogs-extra >/dev/null 2>&1; chattr +C /data").Run()
	fmt.Println("✓ Disabled copy-on-write on testing-kit_windows-data")
}

func downloadOpenSSH() {
	const dest = "windows/oem/OpenSSH-Win64.zip"
	const url = "https://github.com/PowerShell/Win32-OpenSSH/releases/latest/download/OpenSSH-Win64.zip"

	if _, err := os.Stat(dest); err == nil {
		fmt.Printf("⚠  %s already exists — skipping\n", dest)
		return
	}
	fmt.Println("Downloading OpenSSH-Win64.zip for Windows VM setup...")
	if err := downloadToFile(url, dest); err != nil {
		die(fmt.Sprintf("Failed to download OpenSSH-Win64.zip: %v", err))
	}
	fmt.Printf("✓ OpenSSH-Win64.zip downloaded to %s\n", dest)
}

func generateSSHKey() {
	const keyPath = "ssh/id_test"
	const pubPath = "ssh/id_test.pub"

	if _, err := os.Stat(keyPath); err != nil {
		fmt.Println("Generating SSH keypair...")
		if err := os.MkdirAll("ssh", 0o700); err != nil {
			die(fmt.Sprintf("Failed to create ssh/ directory: %v", err))
		}
		cmd := exec.Command("ssh-keygen", "-t", "ed25519", "-N", "", "-f", keyPath, "-C", "testing-kit-ci")
		cmd.Stdout = os.Stdout
		cmd.Stderr = os.Stderr
		if err := cmd.Run(); err != nil {
			die(fmt.Sprintf("ssh-keygen failed: %v", err))
		}
		if err := os.Chmod(keyPath, 0o600); err != nil {
			die(fmt.Sprintf("Failed to set key permissions: %v", err))
		}
		fmt.Println("✓ SSH keypair generated at ssh/id_test")
	} else {
		fmt.Println("⚠  ssh/id_test already exists — skipping key generation")
	}

	tmpl, err := os.ReadFile("windows/oem/install.bat.template")
	if err != nil {
		die(fmt.Sprintf("Failed to read windows/oem/install.bat.template: %v", err))
	}
	pubKey, err := os.ReadFile(pubPath)
	if err != nil {
		die(fmt.Sprintf("Failed to read %s: %v", pubPath, err))
	}
	rendered := strings.ReplaceAll(string(tmpl), "{{PUBLIC_KEY}}", strings.TrimSpace(string(pubKey)))
	if err := os.WriteFile("windows/oem/install.bat", []byte(rendered), 0o644); err != nil {
		die(fmt.Sprintf("Failed to write windows/oem/install.bat: %v", err))
	}
	fmt.Println("✓ windows/oem/install.bat rendered")
}

type multiFlag []string

func (m *multiFlag) String() string        { return strings.Join(*m, ",") }
func (m *multiFlag) Set(v string) error    { *m = append(*m, v); return nil }

func cmdAddExtender(args []string) {
	fs := flag.NewFlagSet("add-extender", flag.ExitOnError)
	installScript := fs.String("install-script", "", "local script to exec as root in adaptixc2")
	overridesFile := fs.String("overrides-file", "", "JSON file of {listener:{},agent:{}} overrides")
	noActivate    := fs.Bool("no-activate", false, "skip activation")
	noRestart     := fs.Bool("no-restart", false, "skip docker restart after activation")
	var overrideFlags multiFlag
	fs.Var(&overrideFlags, "override", "field override: role.key=value (repeatable)")
	fs.Parse(args)

	if fs.NArg() < 1 {
		fmt.Fprintln(os.Stderr, "Usage: testing-kit-cli add-extender <git-url> [flags]")
		os.Exit(1)
	}
	gitURL := fs.Arg(0)

	overrides := map[string]map[string]string{}
	if *overridesFile != "" {
		data, err := os.ReadFile(*overridesFile)
		if err != nil {
			die(fmt.Sprintf("Cannot read overrides file: %v", err))
		}
		if err := json.Unmarshal(data, &overrides); err != nil {
			die(fmt.Sprintf("Invalid JSON in overrides file: %v", err))
		}
	}
	for _, ov := range overrideFlags {
		dotIdx := strings.Index(ov, ".")
		eqIdx  := strings.Index(ov, "=")
		if dotIdx < 0 || eqIdx <= dotIdx {
			die(fmt.Sprintf("Invalid --override format %q; expected role.key=value", ov))
		}
		role := ov[:dotIdx]
		key  := ov[dotIdx+1 : eqIdx]
		val  := ov[eqIdx+1:]
		if overrides[role] == nil {
			overrides[role] = map[string]string{}
		}
		overrides[role][key] = val
	}

	fmt.Printf("Registering extender from %s ...\n", gitURL)
	reqBody, _ := json.Marshal(map[string]any{"git_url": gitURL, "overrides": overrides})
	resp, err := http.Post(apiURL+"/v1/extenders", "application/json", bytes.NewReader(reqBody))
	if err != nil {
		die(fmt.Sprintf("API error: %v", err))
	}
	defer resp.Body.Close()
	respBytes, _ := io.ReadAll(resp.Body)
	if resp.StatusCode != http.StatusOK {
		die(fmt.Sprintf("Registration failed (%d): %s", resp.StatusCode, strings.TrimSpace(string(respBytes))))
	}

	var reg struct {
		ID     string `json:"id"`
		Name   string `json:"name"`
		Status string `json:"status"`
		RequiredFields map[string][]struct {
			Key    string `json:"key"`
			Widget string `json:"widget"`
			Hint   string `json:"hint"`
		} `json:"required_fields"`
	}
	if err := json.Unmarshal(respBytes, &reg); err != nil {
		die(fmt.Sprintf("Cannot parse registration response: %v", err))
	}

	if reg.Status == "needs_input" {
		fmt.Printf("Extender %q registered (id: %s) but missing required fields:\n\n", reg.Name, reg.ID)
		for role, fields := range reg.RequiredFields {
			if len(fields) == 0 {
				continue
			}
			fmt.Printf("  %s:\n", role)
			for _, f := range fields {
				hint := ""
				if f.Hint != "" {
					hint = "  " + f.Hint
				}
				fmt.Printf("    %-30s [%s]%s\n", f.Key, f.Widget, hint)
			}
		}
		fmt.Printf("\nRe-run with --override or --overrides-file to supply missing values.\n")
		os.Exit(1)
	}
	fmt.Printf("✓ Extender %q registered (id: %s)\n", reg.Name, reg.ID)

	if *installScript != "" {
		absScript, err := filepath.Abs(*installScript)
		if err != nil {
			die(fmt.Sprintf("Cannot resolve install script path: %v", err))
		}
		fmt.Printf("Copying install script to adaptixc2 ...\n")
		cpCmd := exec.Command("docker", "cp", absScript, "adaptixc2:/tmp/tk_install.sh")
		cpCmd.Stdout = os.Stdout
		cpCmd.Stderr = os.Stderr
		if err := cpCmd.Run(); err != nil {
			die(fmt.Sprintf("docker cp failed: %v", err))
		}
		fmt.Printf("Running install script in adaptixc2 ...\n")
		execCmd := exec.Command("docker", "exec", "-u", "root", "adaptixc2",
			"bash", "/tmp/tk_install.sh")
		execCmd.Stdout = os.Stdout
		execCmd.Stderr = os.Stderr
		if err := execCmd.Run(); err != nil {
			die(fmt.Sprintf("Install script failed: %v", err))
		}
		fmt.Println("✓ Install script completed")
	}

	if *noActivate {
		return
	}

	fmt.Printf("Activating extender %s ...\n", reg.ID)
	actResp, err := http.Post(apiURL+"/v1/extenders/"+reg.ID+"/activate",
		"application/json", nil)
	if err != nil {
		die(fmt.Sprintf("Activation request failed: %v", err))
	}
	defer actResp.Body.Close()
	actBody, _ := io.ReadAll(actResp.Body)
	if actResp.StatusCode == http.StatusConflict {
		die(fmt.Sprintf("Activation conflict: %s", strings.TrimSpace(string(actBody))))
	}
	if actResp.StatusCode != http.StatusOK {
		die(fmt.Sprintf("Activation failed (%d): %s", actResp.StatusCode, strings.TrimSpace(string(actBody))))
	}
	fmt.Println("✓ Extender activated")

	if *noRestart {
		return
	}

	fmt.Println("Restarting adaptixc2 ...")
	restartCmd := exec.Command("docker", "restart", "adaptixc2")
	restartCmd.Stdout = os.Stdout
	restartCmd.Stderr = os.Stderr
	if err := restartCmd.Run(); err != nil {
		die(fmt.Sprintf("docker restart failed: %v", err))
	}

	fmt.Print("Waiting for adaptixc2")
	for i := 0; i < 150; i++ {
		time.Sleep(2 * time.Second)
		out, err := exec.Command("docker", "exec", "adaptixc2",
			"bash", "-c", "(echo > /dev/tcp/localhost/4321) 2>/dev/null").Output()
		_ = out
		if err == nil {
			fmt.Println("\n✓ adaptixc2 ready")
			return
		}
		fmt.Print(".")
	}
	die("adaptixc2 did not become healthy within 300s after restart")
}
