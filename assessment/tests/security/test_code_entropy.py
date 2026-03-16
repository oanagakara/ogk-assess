from assessment.models import generate_attempt_code 


def test_attempt_code_format():
    code = generate_attempt_code()

    assert len(code) == 8
    assert code.isalnum()
    assert code.upper() == code 


def test_attempt_code_not_constant():
    codes = {generate_attempt_code() for _ in range(200)}

    assert len(codes) == 200
