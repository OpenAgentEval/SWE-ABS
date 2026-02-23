import json
import os
import re
import yaml
from rich.live import Live

def parse_action(self, response: dict) -> dict:
    """Parse actions from the message. Supports multiple environment-specific actions or single action."""
    actions = re.findall(r"```bash\s*\n(.*?)\n```", response["content"], re.DOTALL)

    action_text = actions[0]
    # Check if multiple environment-specific directives are present
    env_action_pattern = r'<env>(.*?)</env>\s*(.*?)(?=(?:<env>|$))'
    matches = re.findall(env_action_pattern, action_text, re.DOTALL)
    
    if matches:
        # Handle multiple environment-specific directives
        env_actions = []
        for env_name, cmd in matches:
            env_name = env_name.strip()
            # Strip trailing && from the command if present
            cmd = cmd.strip()
            if cmd.endswith('&&'):
                cmd = cmd[:-2].strip()
            # Also strip trailing ; if present
            if cmd.endswith(';'):
                cmd = cmd[:-1].strip()

            if env_name == 'All' and cmd:
                return {"action": cmd, "type": "all", **response}
            
            if env_name and cmd:
                env_actions.append((env_name, cmd))
        
        if env_actions:
            return {"actions": env_actions, "type": "multiple", **response}
    
    # Default: handle a single environment directive
    env_name = 'default'
    if '<env>' in action_text:
        try:
            env_name = action_text.split('<env>')[1].split('</env>')[0].strip()
            action_text = action_text.split('</env>')[1].strip()
        except:
            env_name = 'default'
    
    return {"action": action_text.strip(), "env_name": env_name, "type": "single", **response}


# Test function
def test_parse_action():
    # Mock self object
    class MockSelf:
        pass
    mock_self = MockSelf()
    
    # Test case 1: multiple environment-specific directives
    print("测试场景1: 多个环境特定的指令")
    response1 = {
        "content": "这是一个多环境指令示例：\n```bash\n<env>All</env> cd /a && sed -i 's/def _mock_subprocess_run(*args, env=os\.environ, \*\*kwargs):/def _mock_subprocess_run(*args, **kwargs):/' && python tests/runtests.py --verbosity 2 --settings=test_sqlite --parallel 1 tests/dbshell/test_postgresql.py\n```",
        "role": "assistant"
    }
    result1 = parse_action(mock_self, response1)
    print(f"输入: {json.dumps(response1['content'][:100],ensure_ascii=False)}...")
    print(f"输出: {json.dumps({k: v for k, v in result1.items() if k != 'role' and k != 'content'}, indent=2,ensure_ascii=False)}")
    print()
    
    


    # Test case 2: global directive (using All keyword)
    # print("Test case 2: global directive (using All keyword)")
    # response2 = {
    # "content": "Execute the same command in all environments:\n```bash\nAll ls -la\n```",
    #     "role": "assistant"
    # }
    # result2 = parse_action(mock_self, response2)
    # print(f"Input: {json.dumps(response2['content'])}")
    # print(f"Output: {json.dumps({k: v for k, v in result2.items() if k != 'role' and k != 'content'}, indent=2)}")
    # print()
    
    # Test case 3: default environment directive (no env tag)
    # print("Test case 3: default environment directive (no env tag)")
    # response3 = {
    # "content": "Execute command in default environment:\n```bash\necho 'Hello from default env'\n```",
    #     "role": "assistant"
    # }
    # result3 = parse_action(mock_self, response3)
    # print(f"Input: {json.dumps(response3['content'])}")
    # print(f"Output: {json.dumps({k: v for k, v in result3.items() if k != 'role' and k != 'content'}, indent=2)}")
    # print()
    
    # Test case 4: single environment directive (using env tag)
    # print("Test case 4: single environment directive (using env tag)")
    # response4 = {
    # "content": "Execute command in specified environment:\n```bash\n<env>specific_env</env>\necho 'Hello from specific env'\n```",
    #     "role": "assistant"
    # }
    # result4 = parse_action(mock_self, response4)
    # print(f"Input: {json.dumps(response4['content'])}")
    # print(f"Output: {json.dumps({k: v for k, v in result4.items() if k != 'role' and k != 'content'}, indent=2)}")
    # print()
    
    # Test case 5: complex multi-environment directive with blank lines and comments
    # print("Test case 5: complex multi-environment directive with blank lines and comments")
    # response5 = {
    # "content": "Complex multi-environment example:\n```bash\n<env>dev</env>\n# This is the dev environment\necho 'Dev environment'\nls -la /dev\n\n<env>prod</env>\n# This is the prod environment\necho 'Production environment'\nls -la /prod\n```",
    #     "role": "assistant"
    # }
    # result5 = parse_action(mock_self, response5)
    # print(f"Input: {json.dumps(response5['content'][:100])}...")
    # print(f"Output: {json.dumps({k: v for k, v in result5.items() if k != 'role' and k != 'content'}, indent=2)}")
    # print()
    
    # Test case 6: single environment directive with malformed env tag
    # print("Test case 6: single environment directive with malformed env tag")
    # response6 = {
    # "content": "Malformed environment directive:\n```bash\n<env>broken_env\necho 'This should default to default env'\n```",
    #     "role": "assistant"
    # }
    # result6 = parse_action(mock_self, response6)
    # print(f"Input: {json.dumps(response6['content'])}")
    # print(f"Output: {json.dumps({k: v for k, v in result6.items() if k != 'role' and k != 'content'}, indent=2)}")


if __name__ == "__main__":
    test_parse_action()




